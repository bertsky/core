from __future__ import absolute_import

import click

import xml.etree.ElementTree as ET

import xml.dom.minidom as md

from ocrd import init, characterize

@click.command()
@click.option('-w', '--working-dir', default='/tmp', help='Path to store intermediate and result files (default: "/tmp")', type=click.Path(exists=True))
@click.argument('METS_XML', type=click.File('rb'))
def cli(working_dir, mets_xml):
    """
    Perform OCR for a given METS file.
    """

    # read METS
    initializer = init.Initializer()
    initializer.load(mets_xml)

    # set the working dir
    initializer.set_working_dir(working_dir)

    # initialize
    initializer.initialize()

    # image characterization
    characterizer = characterize.Characterizer()
    characterizer.set_handle(initializer.get_handle())
    characterizer.characterize()

    # output
    for ID in initializer.get_handle().page_trees:
        print(md.parseString(ET.tostring(initializer.get_handle().page_trees[ID].getroot(), encoding='utf8', method='xml')).toprettyxml(indent="\t"))
