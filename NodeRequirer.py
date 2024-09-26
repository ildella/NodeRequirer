"""Contains classes for all NodeRequirer commands."""
import sublime
import sublime_plugin
import os
import re
import functools
import json

from .src import utils
from .src.RequireSnippet import RequireSnippet
from .src.modules import core_modules
# from .src.ModuleLoader import ModuleLoader
from .src.node_bridge import node_bridge

WORD_SPLIT_RE = re.compile(r"\W+")
GLOBAL_IMPORT_RE = re.compile(r"^((var|let|const|\s{0,5})\s\w+\s*=\s*)?require\s*\(")
ESLINT_UNDEF_RE = re.compile(r'"(.*)" is not defined')

HAS_REL_PATH_RE = re.compile(r"\.?\.?\/")
IS_EXPORT_LINE_COMMONJS = re.compile(r"exports\.(.*?)=")
IS_EXPORT_LINE_ES6 = re.compile(r"export\s+(var|let|const|function|class)?\s+([^()\[\]{},/*<>%\s-]+)")

class ModuleLoader():

    """Class which handles shared functionality for require commands."""

    def __init__(self, file_name):
        """Constructor for ModuleLoader."""
        self.file_name = file_name
        self.project_folder = self.get_project_folder()
        print('project_folder', self.project_folder)

        if not self.has_package():
            return sublime.error_message(
                'You must have a package.json file in your projects root directory'
            )

    def has_package(self):
        """Check if the package.json is in the project directory."""
        return os.path.exists(
            os.path.join(self.project_folder, 'package.json')
        )

    def get_project_folder(self) -> str:
        """Get the root project folder."""
        dirname = os.path.dirname(self.file_name)
        while dirname:
            pkg = os.path.join(dirname, 'package.json')
            if os.path.exists(pkg):
                return dirname
            parent = os.path.abspath(os.path.join(dirname, os.pardir))
            if parent == dirname:
                break
            dirname = parent

        sublime.error_message(
            'Can\'t find a package.json corresponding to your '
            'project. Please ensure it exists.'
        )

    def get_file_list(self):
        """Return the list of dependencies and local files."""
        files = self.get_local_files() + self.get_dependencies()
        exclude_patterns = utils.file_exclude_patterns()

        def should_include_file(file):
            for pattern in exclude_patterns:
                if pattern in file:
                    return False
            return True
        files = list(filter(should_include_file, files))
        return files

    def get_local_files(self):
        """Load the list of local files."""
        if not self.file_name:
            return []

        dirname = os.path.dirname(self.file_name)
        exclude = utils.dirs_to_exclude()
        local_files = []

        for root, dirs, files in os.walk(self.project_folder, topdown=True):
            if os.path.samefile(root, self.project_folder):
                dirs[:] = [d for d in dirs if d not in exclude]

            for file_name in files:
                if file_name[0] != '.' and file_name != os.path.basename(self.file_name):
                    # Construct the full path relative to the project folder
                    file_path = os.path.relpath(os.path.join(root, file_name), dirname)
                    local_files.append(file_path)  # Keep the relative path as is

        # Prefix with './' to indicate relative paths
        return ["./{}".format(file_path) for file_path in local_files]

    def get_dependencies(self):
        """Load project dependencies."""
        return self.get_package_dependencies()

    def get_package_dependencies(self):
        """Parse the package.json file into a list of dependencies."""
        package_path = os.path.join(self.project_folder, 'package.json')
        with open(package_path, 'r', encoding='UTF-8') as f:
            package_json = json.load(f)

        dependencies = self.get_dependencies_with_type(package_json)
        return dependencies

    def get_dependencies_with_type(self, json):
        """Common function for adding dependencies from package.json."""
        dependencies = []
        for dependency_type in ['dependencies', 'devDependencies', 'optionalDependencies']:
            if dependency_type in json:
                dependencies += json[dependency_type].keys()
        
        all_exports = []
        for dependency in dependencies:
            dependency_exports = self.get_dependency_exports(dependency)
            all_exports.extend(dependency_exports)

        return all_exports

    def get_dependency_exports(self, dependency):
        """Get the exports for a specific dependency."""
        exports = []
        base_path = os.path.join(self.project_folder, 'node_modules', dependency)
        pkg_path = os.path.join(base_path, 'package.json')
        print('exports', base_path, pkg_path)

        if os.path.exists(pkg_path):
            with open(pkg_path, 'r', encoding='UTF-8') as f:
                package_json = json.load(f)
                exports_dict = package_json.get('exports', {})
                exports = self.parse_exports(exports_dict, dependency)

        return exports

    def get_exports(self, module):
        """Get a given module's exports (commonjs style)."""
        if utils.is_core_module(module):
            return self.get_core_module_exports()
        elif utils.is_local_file(module):
            dirname = os.path.dirname(self.file_name)
            path = os.path.join(dirname, module)
            return self.get_exports_in_file(path)
        else:
            return self.get_dependency_module_exports(module)

    def get_core_module_exports(self):
        """Retrieve core module exports dynamically."""
        # Implementation to retrieve core module exports
        sublime.error_message(
            'Parsing node core module exports is not yet '
            'implemented. Feel free to submit a PR!'
        )

    def get_dependency_module_exports(self, module):
        """Get a dependency's exports based on package.json."""
        base_path = os.path.join(self.project_folder, 'node_modules', module)
        pkg_path = os.path.join(base_path, 'package.json')

        if not os.path.exists(pkg_path):
            return []

        with open(pkg_path, 'r', encoding='UTF-8') as f:
            package = json.load(f)

        exports = package.get('exports', {})
        return self.parse_exports(exports, module)

    def parse_exports(self, exports, module):
        """Parse the exports field from package.json."""
        files = []
        if isinstance(exports, dict):
            # Handle the root export
            if '.' in exports:
                files.append(module)  # Add the root module

            for key, value in exports.items():
                if key == '.':
                    continue  # Skip the root export since it's already added
                files.append("{}/{}".format(module, key.lstrip('./')))  # Remove leading './'

        return files

    def get_exports_in_file(self, fpath):
        """Get exports in a given file (commonjs)."""
        exports = []
        if os.path.isdir(fpath):
            fpath = os.path.join(fpath, 'index.js')
        with open(fpath, 'r') as f:
            for line in f:
                result = re.search(IS_EXPORT_LINE_COMMONJS, line)
                if result:
                    exports.append(result.group(1).strip())
                result = re.search(IS_EXPORT_LINE_ES6, line)
                if result:
                    exports.append(result.group(2).strip())

        if not exports:
            sublime.error_message('Unable to find specific exports.')
        return exports


class RequireFromWordCommand(sublime_plugin.TextCommand):

    """Text command for adding require statment from hovering over word."""

    def run(self, edit):
        """Called when the command is run."""
        self.edit = edit
        cursor = self.view.sel()[0]
        word_region = self.view.word(cursor)
        word_text = self.view.substr(word_region)
        import_undefined_vars = utils.get_project_pref('import_undefined_vars',
                                                       view=self.view)

        self.module_loader = ModuleLoader(self.view.file_name())
        self.files = self.module_loader.get_file_list()

        words = [word_text]

        if cursor.empty() and import_undefined_vars:
            undef_vars = self.find_undefined_vars()
            if undef_vars:
                words = undef_vars

        for word in words:
            module = utils.best_fuzzy_match(self.files, word)
            self.view.run_command('require_insert_helper', {
                'args': {
                    'module': module,
                    'type': 'word'
                }
            })

    def find_undefined_vars(self):
        """Executes ESLint if it is installed as local module and finds undefined variables"""
        eslint_path = os.path.join(self.module_loader.project_folder,
                                   'node_modules',
                                   'eslint', 'bin', 'eslint.js')
        if not os.path.exists(eslint_path):
            return []

        args = ['-f', 'compact', '--stdin',
                '--stdin-filename', self.view.file_name()]

        try:
            text = self.view.substr(sublime.Region(0, self.view.size()))
            output = node_bridge(text, eslint_path, args)
        except Exception as e:
            return []

        return list(set([
            re.search(ESLINT_UNDEF_RE, line).group(1)
            for line in output.split('\n')
            if '(no-undef)' in line
        ]))


class RequireCommand(sublime_plugin.TextCommand):

    """Text Command which prompts for a module and inserts it into the file."""

    def run(self, edit, command):
        self.edit = edit

        if command is 'simple':
            # Must copy the core modules so modifying self.files
            # does not change the core_modules list
            self.files = list(core_modules)

            func = self.insert
        # Export Command
        else:
            self.files = []
            self.exports = ['------ Select One or More Options ------']
            self.selected_exports = []
            func = self.show_exports

        self.module_loader = ModuleLoader(self.view.file_name())
        self.files += self.module_loader.get_file_list()
        sublime.active_window().show_quick_panel(
            self.files, self.on_done_call_func(self.files, func))

    def on_path_entered(self, path):
        """When a path is entered, set the project data."""
        sublime.active_window().set_project_data({
            'folders': [{
                'path': path
            }]
        })

    def on_path_changed(self, text):
        """Do nothing when path is changed."""
        return None

    def on_canceled(self):
        """Send error message if user cancels after entering a path."""
        return sublime.error_message(
            'You must configure the absolute path '
            'for your project before using NodeRequirer. '
            'See the readme for more information.'
        )

    def on_done_call_func(self, choices, func):
        """Return a function which is used with sublime list picking."""
        def on_done(index):
            if index >= 0:
                return func(choices[index])

        return on_done

    def insert(self, module):
        """Run the insert helper command with the module selected."""
        self.view.run_command('require_insert_helper', {
            'args': {
                'module': module,
                'type': 'standard'
            }
        })

    def show_exports(self, module=None):
        """Prompt selection of exports for previously selected file."""
        if module is not None:
            self.selected_module = module
            self.exports += self.module_loader.get_exports(module)
        sublime.set_timeout(
            lambda: sublime.active_window().show_quick_panel(
                self.exports,
                self.on_export_done), 10
        )

    def on_export_done(self, index):
        """Handle selection of exports."""
        if index > 0:
            self.exports[0] = ['------ Finish Selecting ------']
            # Add selected export to selected_exports list and
            # remove it from the list
            self.selected_exports.append(self.exports.pop(index))

            if len(self.exports) > 1:
                # Show remaining exports for further selection
                self.show_exports()
            elif len(self.selected_exports) > 0:
                # insert current selected exports
                self.insert_exports()
        elif index == 0 and len(self.selected_exports) > 0:
            # insert current selected exports
            self.insert_exports()

    def insert_exports(self):
        """Run export helper to insert selected exports into file."""
        self.view.run_command('export_insert_helper', {
            'args': {
                'module': self.selected_module,
                'exports': self.selected_exports
            }
        })


class SimpleRequireCommand(RequireCommand):

    """Command that calls the RequireCommand with the type argument simple."""

    def run(self, edit):
        """Called when the SimpleRequireCommand is run."""
        super().run(edit, 'simple')


class ExportRequireCommand(RequireCommand):

    """Command that calls the RequireCommand with the type argument export."""

    def run(self, edit):
        """Called when the ExportRequireCommand is run."""
        super().run(edit, 'export')


class ExportInsertHelperCommand(sublime_plugin.TextCommand):

    """Command that inserts a list of specific exports required."""

    def run(self, edit, args):
        """Insert require statement after the module exports are choosen."""
        module_info = get_module_info(args['module'], self.view)
        module_path = module_info['module_path']
        module_name = module_info['module_name']
        exports = args['exports']
        destructuring = utils.get_pref('destructuring')
        self.edit = edit

        snippet = RequireSnippet(
            module_name,
            module_path,
            should_add_var_name=True,
            should_add_var_statement=True,
            context_allows_semicolon=True,
            view=self.view,
            file_name=self.view.file_name(),
            exports=exports,
            destructuring=destructuring
        )

        content = snippet.get_formatted_code()
        position = self.view.sel()[0].begin()
        self.view.insert(self.edit, position, content)


class RequireInsertHelperCommand(sublime_plugin.TextCommand):

    """Command for inserting a basic require statement."""

    print('Startup')

    def run(self, edit, args):
        """Insert the require statement after the module has been choosen."""
        self.edit = edit

        is_from_word = (args['type'] == 'word')
        module_info = get_module_info(args['module'], self.view)
        module_path = module_info['module_path']
        module_name = module_info['module_name']
        view = self.view

        cursor = view.sel()[0]
        prev_text = view.substr(sublime.Region(0, cursor.begin())).strip()
        next_text = view.substr(
            sublime.Region(cursor.end(), cursor.end() + 80)).strip()
        last_bracket = self.get_last_opened_bracket(prev_text)
        in_brackets = last_bracket in ('(', '[')
        last_word = re.split(WORD_SPLIT_RE, prev_text)[-1]
        should_add_var_statement = (
            not prev_text.endswith(',') and
            last_word not in ('var', 'const', 'let')
        )
        should_add_var_name = (not prev_text.endswith((':', '=')) and
                               not in_brackets)
        context_allows_semicolon = (not next_text.startswith((';', ',')) and
                                    not in_brackets)

        snippet = RequireSnippet(
            module_name,
            module_path,
            should_add_var_name=should_add_var_name,
            should_add_var_statement=should_add_var_statement,
            context_allows_semicolon=context_allows_semicolon,
            view=view,
            file_name=view.file_name()
        )
        if is_from_word:
            self.run_from_word(snippet)
        else:
            self.run_from_command(snippet)

    def run_from_word(self, snippet):
        """Insert a require statement from the ctrl+shift+o command.

        This command mimics the functionality of import-js in that
        the upon the command, the word under the cursor is used to
        determine which module to import. The module is then inserted
        at the bottom of the import list, rather than at the current
        cursor position.
        """

        cursor = self.view.sel()[0]
        prev_region = sublime.Region(0, cursor.begin())
        lines = self.view.lines(prev_region)
        region_for_insertion = None
        found_imports = False
        for line in lines:
            line_text = self.view.substr(line)

            is_global_import = (
                line_text.startswith("import") or
                re.match(GLOBAL_IMPORT_RE, line_text)
            )

            if not is_global_import:
                if found_imports:
                    region_for_insertion = line
                    break
            else:
                found_imports = True

        if region_for_insertion is None:
            region_for_insertion = self.view.line(cursor.begin())

        formatted_code = snippet.get_formatted_code() + '\n'
        self.view.insert(
            self.edit,
            region_for_insertion.begin(),
            formatted_code
        )

    def run_from_command(self, snippet):
        """Run the standard insert snippet command at the cursor position."""
        self.view.run_command('insert_snippet', snippet.get_args())

    def get_last_opened_bracket(self, text):
        """Return the last open bracket before the current cursor position."""
        counts = [(pair, text.count(pair[0]) - text.count(pair[1]))
                  for pair in ('()', '[]', '{}')]

        last_idx = -1
        last_bracket = None
        for pair, count in counts:
            idx = text.rfind(pair[0])
            if idx > last_idx and count > 0:
                (last_idx, last_bracket) = (idx, pair[0])
        return last_bracket

def get_module_info(module_path, view):
    """Get a dictionary with keys for the module_path and the module_name.

    In the case that the module is a node core module, the module_path and
    module_name are the same.
    """
    aliased_to = utils.aliased(module_path, view=view)
    omit_extensions = tuple(utils.get_project_pref('omit_extensions', view=view))
    print('module_path', module_path)
    if aliased_to:
        module_name = aliased_to
    else:
        module_name = os.path.basename(module_path)
        module_name, extension = utils.splitext(module_name)
        print(module_name, extension)

        # When requiring an index.js file, rename the
        # var as the directory directly above
        consume_identical = utils.get_project_pref('dirname_as_index', view=view)
        parent_dir = os.path.split(os.path.dirname(module_path))[-1]
        is_module_index = module_name == 'index' and extension in omit_extensions \
            or consume_identical and module_name == parent_dir

        if is_module_index:
            module_path = os.path.dirname(module_path)
            module_name = os.path.split(module_path)[-1]
            if module_name == '':
                current_file = view.file_module_name()
                directory = os.path.dirname(current_file)
                module_name = os.path.split(directory)[-1]
        # Depending on preferences, remove the file extension
        elif module_path.endswith(omit_extensions):
            module_path = utils.splitext(module_path)[0]

        # Capitalize modules named with dashes
        # i.e. some-thing => SomeThing
        module_name = camelcase(module_name)

    # Fix paths for windows
    if os.sep != '/':
        module_path = module_path.replace(os.sep, '/')
    print(module_path, module_name)
    return {
        'module_path': module_path,
        'module_name': module_name
    }

def camelcase(str):
    split = str.split('-')
    camelCased = split.pop(0)
    for word in split:
        camelCased = camelCased + word[:1].upper() + word[1:]
    return camelCased
