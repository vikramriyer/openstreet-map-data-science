#!/usr/bin/env python
# -*- coding: utf-8 -*-

import xml.etree.cElementTree as ET
import re
from collections import defaultdict
import csv
import codecs
import pprint
import cerberus
import schema

# files
OSM_FILE = "sample.osm"
NODES_PATH = "nodes.csv"
NODE_TAGS_PATH = "nodes_tags.csv"
WAYS_PATH = "ways.csv"
WAY_NODES_PATH = "ways_nodes.csv"
WAY_TAGS_PATH = "ways_tags.csv"

# Make sure the fields order in the csvs matches the column order in the sql table schema
NODE_FIELDS = ['id', 'lat', 'lon', 'user', 'uid', 'version', 'changeset', 'timestamp']
NODE_TAGS_FIELDS = ['id', 'key', 'value', 'type']
WAY_FIELDS = ['id', 'user', 'uid', 'version', 'changeset', 'timestamp']
WAY_TAGS_FIELDS = ['id', 'key', 'value', 'type']
WAY_NODES_FIELDS = ['id', 'node_id', 'position']

SCHEMA = schema.schema

#regex patterns
LEGAL_POSTAL_CODES = re.compile(r'^(411)[0-9]{3}$')
ILLEGAL_POSTAL_CODES = re.compile(r'^(411) ?[0-9] ?[0-9]? [0-9]?$')
path_marg_re = re.compile(r'\b\S+\.?$', re.IGNORECASE)
bots_re = re.compile(r'.*?bot.*?')
phone_number_re = re.compile(r'(91|0)(\s?|\-?)?(20)\s?([0-9]{4}\s?[0-9]{4})|(91|0)(\s?|\-?)?([789][0-9]{9})')

error_postal_codes = []
postcode_mapper = {
    "postal_code": "postcode"
}
street_types = defaultdict(set)
mapping = {
    "Rd": "Road",
    "Path": "Road",
    "Marg": "Road",
    "road": "Road"
}
bots = []

# The data obtained from this function will directly dumped into csv files, which in
# turn will be loaded into mysql
def shape_element(element, node_attr_fields=NODE_FIELDS, way_attr_fields=WAY_FIELDS):
    """ Returns values corresponding to the schema tables
        Args:
            element (ElementTree object): the valid tags in the osm
            node_attr_fields (list): all the attributes mapped to the corresponding NODE_FIELDS
            way_attr_fields (list): all the attributes mapped to the corresponding WAY_FIELDS
        Return:
            node_attributes if element is node
            way_attributes if element is way
    """

    if element.tag == 'node':
        node_attribs, tags = set_node_attributes(element)
        return {'node': node_attribs, 'node_tags': tags}
    elif element.tag == 'way':
        way_attribs, way_nodes, tags = set_way_attributes(element)
        return {'way': way_attribs, 'way_nodes': way_nodes, 'way_tags': tags}

# Uses the above shape_element function to write to csv's
def process_map(file_in, validate):
    """Iteratively process each XML element, validates fields and write to csv(s)
        Args:
            file_in (string): osm file path (current directory in this code)
            validate (boolean): if true, calls validate_element method
        Return:
            Doesn't return
    """

    with codecs.open(NODES_PATH, 'w') as nodes_file, \
            codecs.open(NODE_TAGS_PATH, 'w') as nodes_tags_file, \
            codecs.open(WAYS_PATH, 'w') as ways_file, \
            codecs.open(WAY_NODES_PATH, 'w') as way_nodes_file, \
            codecs.open(WAY_TAGS_PATH, 'w') as way_tags_file:

        nodes_writer = UnicodeDictWriter(nodes_file, NODE_FIELDS)
        node_tags_writer = UnicodeDictWriter(nodes_tags_file, NODE_TAGS_FIELDS)
        ways_writer = UnicodeDictWriter(ways_file, WAY_FIELDS)
        way_nodes_writer = UnicodeDictWriter(way_nodes_file, WAY_NODES_FIELDS)
        way_tags_writer = UnicodeDictWriter(way_tags_file, WAY_TAGS_FIELDS)

        nodes_writer.writeheader()
        node_tags_writer.writeheader()
        ways_writer.writeheader()
        way_nodes_writer.writeheader()
        way_tags_writer.writeheader()

        validator = cerberus.Validator()

        all_elements_file = []

        for element in get_element(file_in, tags=('node', 'way')):
            el = shape_element(element)

            all_elements_file.append(el)

            if el:
                if validate is True:
                    validate_element(el, validator)

                if element.tag == 'node':
                    nodes_writer.writerow(el['node'])
                    node_tags_writer.writerows(el['node_tags'])
                elif element.tag == 'way':
                    ways_writer.writerow(el['way'])
                    way_nodes_writer.writerows(el['way_nodes'])
                    way_tags_writer.writerows(el['way_tags'])

#===================================================================================
#                                  Helpers                                         =
#===================================================================================

# The function will contribute to ways, ways_tags and ways_nodes tables in mysql
def set_way_attributes(element):
    """Returns a shaped element to fit the corresponding mysql table for 'way' element
        Args:
            element (ElementTree object): the valid tags in the osm
        Return:
            return way_attributes, way_nodes, tags
    """

    way_attributes = {}
    way_nodes = []
    tags = []

    way_attributes['id'] = element.attrib['id']
    way_attributes['version'] = element.attrib['version']
    way_attributes['timestamp'] = element.attrib['timestamp']
    way_attributes['changeset'] = element.attrib['changeset']
    way_attributes['user'] = element.attrib['user']
    way_attributes['uid'] = element.attrib['uid']

    find_bots(way_attributes['user'])

    position_counter = 0
    for child in element:
        way_tags = {}
        if child.tag == 'nd':
            way_nodes.append({'id': element.attrib['id'], 'node_id': child.attrib['ref'], 'position': position_counter})
            position_counter += 1
        else:
            value = update_name(child.attrib['v'], mapping)
            if ':' in child.attrib['k']:
                keys_values = child.attrib['k'].split(":", 1)
                key = update_name(keys_values[1], postcode_mapper)
                if key == 'postcode':
                    value = validate_postcode(value)
                if key == 'phone':
                    value = validate_phone_numbers(child.attrib['v'])
                way_tags['type'] = keys_values[0]
                way_tags['key'] = keys_values[1]
                way_tags['value'] = value
                way_tags['id'] = element.attrib['id']
                tags.append(dict(way_tags))
            else:
                way_tags['type'] = 'regular'
                way_tags['key'] = child.attrib['k']
                way_tags['value'] = child.attrib['v']
                way_tags['id'] = element.attrib['id']
                tags.append(way_tags)

    return way_attributes, way_nodes, tags

# The function will contribute to nodes and nodes_tags tables in mysql
def set_node_attributes(element):
    """Returns a shaped element to fit the corresponding mysql table for 'node' element
        Args:
            element (ElementTree object): the valid tags in the osm
        Return:
            return node_attributes, tags
    """

    node_attributes = {}
    tags = []

    node_attributes['id'] = element.attrib['id']
    node_attributes['version'] = element.attrib['version']
    node_attributes['timestamp'] = element.attrib['timestamp']
    node_attributes['changeset'] = element.attrib['changeset']
    node_attributes['lat'] = element.attrib['lat']
    node_attributes['lon'] = element.attrib['lon']
    node_attributes['user'] = element.attrib['user']
    node_attributes['uid'] = element.attrib['uid']

    find_bots(node_attributes['user'])

    for child in element:
        node_tags = {}
        value = update_name(child.attrib['v'], mapping)
        if ':' in child.attrib['k']:
            keys_values = child.attrib['k'].split(":", 1)
            key = update_name(keys_values[1], postcode_mapper)
            if key == 'postcode':
                value = validate_postcode(value)
            node_tags['type'] = keys_values[0]
            node_tags['key'] = key
            node_tags['value'] = value
            node_tags['id'] = element.attrib['id']
            tags.append(dict(node_tags))
        else:
            if child.attrib['k'] == 'phone':
                value = validate_phone_numbers(child.attrib['v'])
            node_tags['type'] = 'regular'
            node_tags['key'] = child.attrib['k']
            node_tags['value'] = value
            node_tags['id'] = element.attrib['id']
            tags.append(dict(node_tags))

    return node_attributes, tags

def find_bots(username):
    """ If the user is bot, adds to the global list bots
        Args:
            username (str): name of the contributor
        Return:
            Doesn't return
    """
    if bots_re.search(username):
        bots.append(username)

def validate_phone_numbers(phone_number):
    """Validates if the given number is mobile (India) /landline (Pune) number
        Args: 
            phone_number (str): phone number 
        Return:
            audited phone_number
    """

    all_matches = phone_number_re.findall(phone_number)
    for match in all_matches[0]:
        if len(match) > 2:
            phone_number = str(match)

    return phone_number

def validate_postcode(code):

    """Validates if the given code is valid postal code (PUNE)
        Args:
            code (str): postal/zip code
        Return:
            audited code
    """

    if not LEGAL_POSTAL_CODES.search(code):
        if ILLEGAL_POSTAL_CODES.search(code):
            code = re.sub(' |_|-','',code)
        else:
            error_postal_codes.append(code)
            code = ''
    return code

# Important function which is faster than usage of list which stores the elements in memory.
# Using the "yield" will ensure that the data once accessed will be erased rather than storing
# in memory till the process releases resources after its termination
def get_element(osm_file, tags=('node', 'way', 'relation')):
    """Yield element if it is the right type of tag
        Args:
            osm_file (str): osm file path
            tags (tuple): any of the top level tags (node, way, relation)
        Return:
            returns nothing, but yeild element mimics the return
    """

    context = ET.iterparse(osm_file, events=('start', 'end'))
    _, root = next(context)
    for event, elem in context:
        if event == 'end' and elem.tag in tags:
            yield elem
            root.clear()

def validate_element(element, validator, schema=SCHEMA):
    """Raise ValidationError if element does not match schema
        Args:
            element (ElementTree object): the valid tags in the osm
            validator (cerberus validator object): validates fields with attributes
        Return:
            returns nothing  
    """

    if validator.validate(element, schema) is not True:
        field, errors = next(validator.errors.iteritems())
        message_string = "\nElement of type '{0}' has the following errors:\n{1}"
        error_string = pprint.pformat(errors)

        raise Exception(message_string.format(field, error_string))

class UnicodeDictWriter(csv.DictWriter, object):
    """Extend csv.DictWriter to handle Unicode input"""

    def writerow(self, row):
        super(UnicodeDictWriter, self).writerow({
            k: (v.encode('utf-8') if isinstance(v, unicode) else v) for k, v in row.iteritems()
        })

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)

def read_osm_file():
    """Returns an iterator providing (event, elem) pairs
        Args:
            none
        Returns:
            iterator over the osm file
    """

    return ET.iterparse(OSM_FILE, events=('start','end'))

def get_all_top_level_tags():
    """Get all the top level tags (usage: to get used to the data)]
        Args:
            none
        Returns:
            returns all top level tags
    """

    all_top_level_tags = {}
    context = read_osm_file()
    _, root = next(context)

    for event, elem in context:
        tag = elem.tag
        if event == 'start':
            if tag in all_top_level_tags:
                all_top_level_tags[tag] += 1
            else:
                all_top_level_tags[tag] = 1

    return all_top_level_tags

def is_street_type(street_type):
    """Returns a boolean if the condition matches
        Args: 
            street_type (str): type of the street
        Return:
            return true if type is street
            false if not
    """

    return street_type == "addr:street"

def audit_data():
    """Returns a dict-set containing the erroneous street type and various occurrences
        Args:
            none
        Return:
            Returns a street type and its occurrences
    """

    context = read_osm_file()
    _,root = next(context)

    for event, elem in context:
        tag_name = elem.tag
        if tag_name == 'way' or tag_name == 'node':
            for tag in elem.iter("tag"):
                if is_street_type(tag.attrib['k']):
                    audit_street_types(tag.attrib['v'])

    return street_types

def audit_street_types(street_name):
    """Helper to the audit_data method to match the regex
        Args:
            street_name: name of the street
        Return:
            doesnt return
    """

    match = path_marg_re.search(street_name)

    if match:
        street_types[match.group()].add(street_name)

def update_name(name, mapping):
    """If the value of an attribute matches the erroneous list, returns the corrected one
        Args:
            name (str): various forms of street names
            mapping (dict): dictionary to map linguistic forms of Road
        Return:
            returns the mapped name if present in mapping dict
            else returns the argument as it is
    """

    values = re.split(" ",name)

    for key, value in mapping.iteritems():
        if key in values:
            name = name.replace(key, value)
    return name

if __name__ == '__main__':

    # Algo:
    # Step 1: Audit the data to generate a list of problematic street types
    # Step 2: Use the shape element function to map the osm file to fit the relational
    #         database tables
    # Step 3: dump the above results into a csv

    #print "1. get all to level tags"
    #all_top_level_tags = get_all_top_level_tags()
    #print all_top_level_tags
    #print "\n*************\n"

    street_tp = audit_data()

    if len(bots) > 1:
        print "The bot users are:: " + str(set(bots)) + "\n*************\n"

    if len(error_postal_codes) > 1:
        print "List of malformed postal codes not caught in any pattern::\n" + str(set(error_postal_codes))

    process_map(OSM_FILE, validate=True)
