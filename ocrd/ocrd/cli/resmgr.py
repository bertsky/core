"""
OCR-D CLI: management of processor resources

.. click:: ocrd.cli.resmgr:resmgr_cli
    :prog: ocrd resmgr
    :nested: full
"""
import sys
from pathlib import Path
from distutils.spawn import find_executable as which

import requests
import click

from ocrd_utils import (
    initLogging,
    getLogger,
    RESOURCE_LOCATIONS
)

from ..resource_manager import OcrdResourceManager

def print_resources(executable, reslist, resmgr):
    print('%s' % executable)
    for resdict in reslist:
        print('- %s %s (%s)\n  %s' % (
            resdict['name'],
            '@ %s' % resmgr.resource_dir_to_location(resdict['path']) if 'path' in resdict else '',
            resdict['url'],
            resdict['description']
        ))
    print()

@click.group("resmgr")
def resmgr_cli():
    """
    Managing processor resources
    """
    initLogging()

@resmgr_cli.command('list-available')
@click.option('-e', '--executable', help='Show only resources for executable EXEC', metavar='EXEC')
def list_available(executable=None):
    """
    List available resources
    """
    resmgr = OcrdResourceManager()
    for executable, reslist in resmgr.list_available(executable):
        print_resources(executable, reslist, resmgr)

@resmgr_cli.command('list-installed')
@click.option('-e', '--executable', help='Show only resources for executable EXEC', metavar='EXEC')
def list_installed(executable=None):
    """
    List installed resources
    """
    resmgr = OcrdResourceManager()
    ret = []
    for executable, reslist in resmgr.list_installed(executable):
        print_resources(executable, reslist, resmgr)

@resmgr_cli.command('download')
@click.option('-n', '--any-url', help='URL of unregistered resource to download/copy from', default='')
@click.option('-t', '--resource-type', help='Type of resource', type=click.Choice(['file', 'github-dir', 'tarball']), default='file')
@click.option('-P', '--path-in-archive', help='Path to extract in case of archive type', default='.')
@click.option('-a', '--allow-uninstalled', help="Allow installing resources for uninstalled processors", is_flag=True)
@click.option('-o', '--overwrite', help='Overwrite existing resources', is_flag=True)
@click.option('-l', '--location', help='Where to store resources', type=click.Choice(RESOURCE_LOCATIONS), default='data', show_default=True)
@click.argument('executable', required=True)
@click.argument('name', required=False)
def download(any_url, resource_type, path_in_archive, allow_uninstalled, overwrite, location, executable, name):
    """
    Download resource NAME for processor EXECUTABLE.

    NAME is the name of the resource made available by downloading or copying.

    If NAME is '*' (asterisk), then download all known registered resources for this processor.

    If ``--any-url=URL`` or ``-n URL`` is given, then URL is accepted regardless of registered resources for ``NAME``.
    (This can be used for unknown resources or for replacing registered resources.)

    If ``--resource-type`` is set to `tarball`, then that archive gets unpacked after download,
    and its ``--path-in-archive`` will subsequently be renamed to NAME.
    """
    log = getLogger('ocrd.cli.resmgr')
    resmgr = OcrdResourceManager()
    basedir = resmgr.location_to_resource_dir(location)
    if executable != '*' and not name:
        log.error("Unless EXECUTABLE ('%s') is the '*' wildcard, NAME is required" % executable)
        sys.exit(1)
    elif executable == '*':
        executable = None
    if name == '*':
        name = None
    is_url = (any_url.startswith('https://') or any_url.startswith('http://')) if any_url else False
    is_filename = Path(any_url).exists() if any_url else False
    if executable and not which(executable):
        if not allow_uninstalled:
            log.error("Executable '%s' is not installed. " \
                      "To download resources anyway, use the -a/--allow-uninstalled flag", executable)
            sys.exit(1)
        else:
            log.info("Executable %s is not installed, but " \
                     "downloading resources anyway", executable)
    reslist = resmgr.find_resources(executable=executable, name=name)
    if not reslist:
        log.info("No resources found in registry")
        if executable and name:
            reslist = [(executable, {'url': '???', 'name': name,
                                     'type': resource_type,
                                     'path_in_archive': path_in_archive})]
    for executable, resdict in reslist:
        if 'size' in resdict:
            registered = "registered"
        else:
            registered = "unregistered"
        if any_url:
            resdict['url'] = any_url
        if resdict['url'] == '???':
            log.warning("Cannot download user resource %s", resdict['name'])
            continue
        if resdict['url'].startswith('https://') or resdict['url'].startswith('http://'):
            log.info("Downloading %s resource '%s' (%s)", registered, resdict['name'], resdict['url'])
            with requests.get(resdict['url'], stream=True) as r:
                resdict['size'] = int(r.headers.get('content-length'))
        else:
            log.info("Copying %s resource '%s' (%s)", registered, resdict['name'], resdict['url'])
            urlpath = Path(resdict['url'])
            resdict['url'] = str(urlpath.resolve())
            resdict['size'] = urlpath.stat().st_size
        with click.progressbar(length=resdict['size']) as bar:
            fpath = resmgr.download(
                executable,
                resdict['url'],
                name=resdict['name'],
                resource_type=resdict.get('type', resource_type),
                path_in_archive=resdict.get('path_in_archive', path_in_archive),
                overwrite=overwrite,
                size=resdict['size'],
                no_subdir=location == 'cwd',
                basedir=basedir,
                progress_cb=lambda delta: bar.update(delta)
            )
        if registered == 'unregistered':
            log.info("%s resource '%s' (%s) not a known resource, creating stub in %s'", executable, name, any_url, resmgr.user_list)
            resmgr.add_to_user_database(executable, fpath, url=any_url)
        log.info("Installed resource %s under %s", resdict['url'], fpath)
        log.info("Use in parameters as '%s'", resmgr.parameter_usage(resdict['name'], usage=resdict.get('parameter_usage', 'as-is')))
