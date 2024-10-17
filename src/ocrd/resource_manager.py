from pathlib import Path
from os.path import join
from os import environ, listdir, getcwd, unlink
from shutil import copytree, rmtree, copy
from fnmatch import filter as apply_glob
from datetime import datetime
from tarfile import open as open_tarfile
from urllib.parse import urlparse, unquote
from zipfile import ZipFile

import requests
from gdown.parse_url import parse_url as gparse_url
from gdown.download import get_url_from_gdrive_confirmation
from yaml import safe_load, safe_dump

# pylint: disable=wrong-import-position

# https://github.com/OCR-D/core/issues/867
# https://stackoverflow.com/questions/50900727/skip-converting-entities-while-loading-a-yaml-string-using-pyyaml
import yaml.constructor
yaml.constructor.SafeConstructor.yaml_constructors['tag:yaml.org,2002:timestamp'] = \
    yaml.constructor.SafeConstructor.yaml_constructors['tag:yaml.org,2002:str']

# pylint: enable=wrong-import-position

# pylint: enable=wrong-import-position

from ocrd_validators import OcrdResourceListValidator
from ocrd_utils import getLogger, directory_size, get_moduledir, guess_media_type, config
from ocrd_utils.os import get_processor_resource_types, list_all_resources, pushd_popd, get_ocrd_tool_json
from .constants import RESOURCE_LIST_FILENAME, RESOURCE_USER_LIST_COMMENT


class OcrdResourceManager:

    """
    Managing processor resources
    """
    def __init__(self, userdir=None, xdg_config_home=None, xdg_data_home=None, skip_init=False):
        self.log = getLogger('ocrd.resource_manager')
        self.database = {}

        self._xdg_data_home = xdg_data_home
        self._xdg_config_home = xdg_config_home
        self._userdir = userdir
        self.user_list = Path(self.xdg_config_home, 'ocrd', 'resources.yml')

        if not skip_init:
            self.load_resource_list(Path(RESOURCE_LIST_FILENAME))
            if not self.user_list.exists():
                if not self.user_list.parent.exists():
                    self.user_list.parent.mkdir(parents=True)
                self.save_user_list()
            self.load_resource_list(self.user_list)

    @property
    def userdir(self):
        if not self._userdir:
            self._userdir = config.HOME
        return self._userdir

    @property
    def xdg_data_home(self):
        if not self._xdg_data_home:
            self._xdg_data_home = config.XDG_DATA_HOME
        return self._xdg_data_home

    @property
    def xdg_config_home(self):
        if self._xdg_config_home:
            return self._xdg_config_home
        return config.XDG_CONFIG_HOME

    def save_user_list(self, database=None):
        if not database:
            database = self.database
        with open(self.user_list, 'w', encoding='utf-8') as f:
            f.write(RESOURCE_USER_LIST_COMMENT)
            f.write('\n')
            f.write(safe_dump(database))

    def load_resource_list(self, list_filename, database=None):
        if not database:
            database = self.database
        if list_filename.is_file():
            with open(list_filename, 'r', encoding='utf-8') as f:
                list_loaded = safe_load(f) or {}
            report = OcrdResourceListValidator.validate(list_loaded)
            if not report.is_valid:
                self.log.error('\n'.join(report.errors))
                raise ValueError(f"Resource list {list_filename} is invalid!")
            for executable, resource_list in list_loaded.items():
                if executable not in database:
                    database[executable] = []
                # Prepend, so user provided is sorted before builtin
                database[executable] = list_loaded[executable] + database[executable]
        return database

    def list_available(self, executable=None, dynamic=True, name=None, database=None, url=None):
        """
        List models available for download by processor
        """
        if not database:
            database = self.database
        if not executable:
            return database.items()
        if dynamic:
            skip_executables = ["ocrd-cis-data", "ocrd-import", "ocrd-make"]
            for exec_dir in environ['PATH'].split(':'):
                for exec_path in Path(exec_dir).glob(f'{executable}'):
                    if not exec_path.name.startswith('ocrd-'):
                        self.log.warning(f"OCR-D processor executable '{exec_path}' has no 'ocrd-' prefix")
                    if exec_path.name in skip_executables:
                        self.log.debug(f"Not an OCR-D processor CLI, skipping '{exec_path}'")
                        continue
                    self.log.debug(f"Inspecting '{exec_path} --dump-json' for resources")
                    ocrd_tool = get_ocrd_tool_json(exec_path)
                    for resdict in ocrd_tool.get('resources', ()):
                        if exec_path.name not in database:
                            database[exec_path.name] = []
                        database[exec_path.name].insert(0, resdict)
            database = self._dedup_database(database)
        found = False
        ret = []
        for k in database:
            if apply_glob([k], executable):
                found = True
                restuple = (k, [])
                ret.append(restuple)
                for resdict in database[k]:
                    if name and resdict['name'] != name:
                        continue
                    if url and resdict['url'] != url:
                        continue
                    restuple[1].append(resdict)
        if not found:
            ret = [(executable, [])]
        return ret

    def list_installed(self, executable=None):
        """
        List installed resources, matching with registry by ``name``
        """
        ret = []
        if executable:
            all_executables = [executable]
        else:
            # resources we know about
            all_executables = list(self.database.keys())
            # resources in the file system
            parent_dirs = [join(x, 'ocrd-resources') for x in [self.xdg_data_home, '/usr/local/share']]
            for parent_dir in parent_dirs:
                if Path(parent_dir).exists():
                    all_executables += [x for x in listdir(parent_dir) if x.startswith('ocrd-')]
        for this_executable in set(all_executables):
            reslist = []
            mimetypes = get_processor_resource_types(this_executable)
            moduledir = get_moduledir(this_executable)
            for res_filename in list_all_resources(this_executable, moduled=moduledir, xdg_data_home=self.xdg_data_home):
                res_filename = Path(res_filename)
                if not '*/*' in mimetypes:
                    if res_filename.is_dir() and not 'text/directory' in mimetypes:
                        continue
                    if res_filename.is_file() and ['text/directory'] == mimetypes:
                        continue
                res_name = res_filename.name
                res_type = 'file' if res_filename.is_file() else 'directory'
                res_size = res_filename.stat().st_size if res_filename.is_file() else directory_size(res_filename)
                resdict_list = [x for x in self.database.get(this_executable, []) if x['name'] == res_name]
                if resdict_list:
                    resdict = resdict_list[0]
                elif str(res_filename.parent) == moduledir:
                    resdict = {
                        'name': res_name, 
                        'url': str(res_filename), 
                        'description': 'Found at module', 
                        'type': res_type,
                        'size': res_size
                    }
                else:
                    resdict = self.add_to_user_database(this_executable, res_filename, resource_type=res_type)
                resdict['path'] = str(res_filename)
                reslist.append(resdict)
            ret.append((this_executable, reslist))
        return ret

    def add_to_user_database(self, executable, res_filename, url=None, resource_type='file'):
        """
        Add a stub entry to the user resource.yml
        """
        res_name = Path(res_filename).name
        self.log.info(f"{executable} resource '{res_name}' ({str(res_filename)}) not a known resource, "
                      f"creating stub in {self.user_list}'")
        if Path(res_filename).is_dir():
            res_size = directory_size(res_filename)
        else:
            res_size = Path(res_filename).stat().st_size
        with open(self.user_list, 'r', encoding='utf-8') as f:
            user_database = safe_load(f) or {}
        if executable not in user_database:
            user_database[executable] = []
        resources_found = self.list_available(executable=executable, name=res_name, database=user_database)[0][1]
        if not resources_found:
            resdict = {
                'name': res_name,
                'url': url if url else '???',
                'description': f'Found at {self.resource_dir_to_location(res_filename)} on {datetime.now()}',
                'version_range': '???',
                'type': resource_type,
                'size': res_size
            }
            user_database[executable].append(resdict)
        else:
            resdict = resources_found[0]
        self.save_user_list(user_database)
        self.load_resource_list(self.user_list)
        return resdict

    @property
    def default_resource_dir(self):
        return self.location_to_resource_dir('data')

    def location_to_resource_dir(self, location):
        return '/usr/local/share/ocrd-resources' if location == 'system' else \
                join(self.xdg_data_home, 'ocrd-resources') if location == 'data' else \
                getcwd()

    def resource_dir_to_location(self, resource_path):
        resource_path = str(resource_path)
        return 'system' if resource_path.startswith('/usr/local/share/ocrd-resources') else \
               'data' if resource_path.startswith(join(self.xdg_data_home, 'ocrd-resources')) else \
               'cwd' if resource_path.startswith(getcwd()) else \
               resource_path

    @staticmethod
    def parameter_usage(name, usage='as-is'):
        if usage == 'as-is':
            return name
        elif usage == 'without-extension':
            return Path(name).stem
        raise ValueError(f"No such usage '{usage}'")

    @staticmethod
    def _download_impl(url, filename, progress_cb=None, size=None):
        log = getLogger('ocrd.resource_manager._download_impl')
        log.info(f"Downloading {url} to {filename}")
        try:
            gdrive_file_id, is_gdrive_download_link = gparse_url(url, warning=False)
            if gdrive_file_id:
                if not is_gdrive_download_link:
                    url = f"https://drive.google.com/uc?id={gdrive_file_id}"
                try:
                    with requests.get(url, stream=True) as r:
                        if "Content-Disposition" not in r.headers:
                            url = get_url_from_gdrive_confirmation(r.text)
                except RuntimeError as e:
                    log.warning("Cannot unwrap Google Drive URL: %s", e)
            with open(filename, 'wb') as f:
                with requests.get(url, stream=True) as r:
                    r.raise_for_status()
                    for data in r.iter_content(chunk_size=4096):
                        if progress_cb:
                            progress_cb(len(data))
                        f.write(data)
        except Exception as e:
            rmtree(filename, ignore_errors=True)
            Path(filename).unlink(missing_ok=True)
            raise e

    @staticmethod
    def _copy_file(src, dst, progress_cb=None):
        log = getLogger('ocrd.resource_manager._copy_file')
        log.info(f"Copying file {src} to {dst}")
        with open(dst, 'wb') as f_out, open(src, 'rb') as f_in:
            while True:
                chunk = f_in.read(4096)
                if chunk:
                    f_out.write(chunk)
                    if progress_cb:
                        progress_cb(len(chunk))
                else:
                    break

    @staticmethod
    def _copy_dir(src, dst, progress_cb=None):
        log = getLogger('ocrd.resource_manager._copy_dir')
        log.info(f"Copying dir recursively from {src} to {dst}")
        if not Path(src).is_dir():
            raise ValueError(f"The source is not a directory: {src}")
        Path(dst).mkdir(parents=True, exist_ok=True)
        for child in Path(src).rglob('*'):
            child_dst = Path(dst) / child.relative_to(src)
            if Path(child).is_dir():
                OcrdResourceManager._copy_dir(child, child_dst, progress_cb)
            else:
                OcrdResourceManager._copy_file(child, child_dst, progress_cb)

    @staticmethod
    def _copy_impl(src_filename, filename, progress_cb=None):
        log = getLogger('ocrd.resource_manager._copy_impl')
        log.info(f"Copying {src_filename} to {filename}")
        if Path(src_filename).is_dir():
            OcrdResourceManager._copy_dir(src_filename, filename, progress_cb)
        else:
            OcrdResourceManager._copy_file(src_filename, filename, progress_cb)

    # TODO Proper caching (make head request for size, If-Modified etc)
    def download(
        self, executable, url, basedir, overwrite=False, no_subdir=False, name=None, resource_type='file',
        path_in_archive='.', progress_cb=None,
    ):
        """
        Download a resource by URL
        """
        log = getLogger('ocrd.resource_manager.download')
        destdir = Path(basedir) if no_subdir else Path(basedir, executable)
        if not name:
            url_parsed = urlparse(url)
            name = Path(unquote(url_parsed.path)).name
        fpath = Path(destdir, name)
        is_url = url.startswith('https://') or url.startswith('http://')
        if fpath.exists():
            if not overwrite:
                fpath_type = 'Directory' if fpath.is_dir() else 'File'
                log.warning(f"{fpath_type} {fpath} already exists but --overwrite is not set, skipping the download")
                # raise FileExistsError(f"{fpath_type} {fpath} already exists but --overwrite is not set")
                return fpath
            if fpath.is_dir():
                log.info(f"Removing existing target directory {fpath}")
                rmtree(str(fpath))
            else:
                log.info(f"Removing existing target file {fpath}")
                unlink(str(fpath))
        destdir.mkdir(parents=True, exist_ok=True)
        if resource_type in ('file', 'directory'):
            if is_url:
                self._download_impl(url, fpath, progress_cb)
            else:
                self._copy_impl(url, fpath, progress_cb)
        elif resource_type == 'archive':
            archive_fname = 'download.tar.xx'
            with pushd_popd(tempdir=True) as tempdir:
                if is_url:
                    self._download_impl(url, archive_fname, progress_cb)
                else:
                    self._copy_impl(url, archive_fname, progress_cb)
                Path('out').mkdir()
                with pushd_popd('out'):
                    mimetype = guess_media_type(f'../{archive_fname}', fallback='application/octet-stream')
                    log.info(f"Extracting {mimetype} archive to {tempdir}/out")
                    if mimetype == 'application/zip':
                        with ZipFile(f'../{archive_fname}', 'r') as zipf:
                            zipf.extractall()
                    elif mimetype in ('application/gzip', 'application/x-xz'):
                        with open_tarfile(f'../{archive_fname}', 'r:*') as tar:
                            tar.extractall()
                    else:
                        raise RuntimeError(f"Unable to handle extraction of {mimetype} archive {url}")
                    log.info(f"Copying '{path_in_archive}' from archive to {fpath}")
                    if Path(path_in_archive).is_dir():
                        copytree(path_in_archive, str(fpath))
                    else:
                        copy(path_in_archive, str(fpath))
        return fpath

    def _dedup_database(self, database=None, dedup_key='name'):
        """
        Deduplicate resources by name
        """
        if not database:
            database = self.database
        for executable, reslist in database.items():
            reslist_dedup = []
            for resdict in reslist:
                if not any(r[dedup_key] == resdict[dedup_key] for r in reslist_dedup):
                    reslist_dedup.append(resdict)
            database[executable] = reslist_dedup
        return database
