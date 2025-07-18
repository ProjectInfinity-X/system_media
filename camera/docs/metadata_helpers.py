#
# Copyright (C) 2012 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""
A set of helpers for rendering Mako templates with a Metadata model.
"""

import metadata_model
import re
import markdown
import textwrap
import sys
import bs4
# Monkey-patch BS4. WBR element must not have an end tag.
bs4.builder.HTMLTreeBuilder.empty_element_tags.add("wbr")

from collections import OrderedDict, defaultdict
from operator import itemgetter
from os import path

# Relative path from HTML file to the base directory used by <img> tags
IMAGE_SRC_METADATA="images/camera2/metadata/"

# Prepend this path to each <img src="foo"> in javadocs
JAVADOC_IMAGE_SRC_METADATA="/reference/" + IMAGE_SRC_METADATA
NDKDOC_IMAGE_SRC_METADATA="../" + IMAGE_SRC_METADATA

#Corresponds to Android Q, where the camera VNDK was added (minor version 4 and vndk version 29).
# Minor version and vndk version must correspond to the same release
FRAMEWORK_CAMERA_VNDK_HAL_MINOR_VERSION = 4
FRAMEWORK_CAMERA_VNDK_STARTING_VERSION =  29

_context_buf = None
_enum = None

def _is_sec_or_ins(x):
  return isinstance(x, metadata_model.Section) or    \
         isinstance(x, metadata_model.InnerNamespace)

##
## Metadata Helpers
##

def find_all_sections(root):
  """
  Find all descendants that are Section or InnerNamespace instances.

  Args:
    root: a Metadata instance

  Returns:
    A list of Section/InnerNamespace instances

  Remarks:
    These are known as "sections" in the generated C code.
  """
  return root.find_all(_is_sec_or_ins)

def find_all_sections_filtered(root, visibility):
  """
  Find all descendants that are Section or InnerNamespace instances that do not
  contain entries of the supplied visibility

  Args:
    root: a Metadata instance
    visibilities: An iterable of visibilities to filter against

  Returns:
    A list of Section/InnerNamespace instances

  Remarks:
    These are known as "sections" in the generated C code.
  """
  sections = root.find_all(_is_sec_or_ins)

  filtered_sections = []
  for sec in sections:
    if not any(filter_visibility(find_unique_entries(sec), visibility)):
      filtered_sections.append(sec)

  return filtered_sections


def find_parent_section(entry):
  """
  Find the closest ancestor that is either a Section or InnerNamespace.

  Args:
    entry: an Entry or Clone node

  Returns:
    An instance of Section or InnerNamespace
  """
  return entry.find_parent_first(_is_sec_or_ins)

# find uniquely named entries (w/o recursing through inner namespaces)
def find_unique_entries(node):
  """
  Find all uniquely named entries, without recursing through inner namespaces.

  Args:
    node: a Section or InnerNamespace instance

  Yields:
    A sequence of MergedEntry nodes representing an entry

  Remarks:
    This collapses multiple entries with the same fully qualified name into
    one entry (e.g. if there are multiple entries in different kinds).
  """
  if not isinstance(node, metadata_model.Section) and    \
     not isinstance(node, metadata_model.InnerNamespace):
      raise TypeError("expected node to be a Section or InnerNamespace")

  d = OrderedDict()
  # remove the 'kinds' from the path between sec and the closest entries
  # then search the immediate children of the search path
  search_path = isinstance(node, metadata_model.Section) and node.kinds \
                or [node]
  for i in search_path:
      for entry in i.entries:
          d[entry.name] = entry

  for k,v in d.items():
      yield v.merge()

def path_name(node):
  """
  Calculate a period-separated string path from the root to this element,
  by joining the names of each node and excluding the Metadata/Kind nodes
  from the path.

  Args:
    node: a Node instance

  Returns:
    A string path
  """

  isa = lambda x,y: isinstance(x, y)
  fltr = lambda x: not isa(x, metadata_model.Metadata) and \
                   not isa(x, metadata_model.Kind)

  path = node.find_parents(fltr)
  path = list(path)
  path.reverse()
  path.append(node)

  return ".".join((i.name for i in path))

def ndk(name):
  """
  Return the NDK version of given name, which replace
  the leading "android" to "acamera"

  Args:
    name: name string of an entry

  Returns:
    A NDK version name string of the input name
  """
  name_list = name.split(".")
  if name_list[0] == "android":
    name_list[0] = "acamera"
  return ".".join(name_list)

def protobuf_type(entry):
  """
  Return the protocol buffer message type for input metadata entry.
  Only support types used by static metadata right now

  Returns:
    A string of protocol buffer type. Ex: "optional int32" or "repeated RangeInt"
  """
  typeName = None
  if entry.typedef is None:
    typeName = entry.type
  else:
    typeName = entry.typedef.name

  typename_to_protobuftype = {
    "rational"               : "Rational",
    "size"                   : "Size",
    "sizeF"                  : "SizeF",
    "rectangle"              : "Rect",
    "streamConfigurationMap" : "StreamConfigurations",
    "mandatoryStreamCombination" : "MandatoryStreamCombination",
    "rangeInt"               : "RangeInt",
    "rangeLong"              : "RangeLong",
    "rangeFloat"             : "RangeFloat",
    "colorSpaceTransform"    : "ColorSpaceTransform",
    "blackLevelPattern"      : "BlackLevelPattern",
    "byte"                   : "int32", # protocol buffer don't support byte
    "boolean"                : "bool",
    "float"                  : "float",
    "double"                 : "double",
    "int32"                  : "int32",
    "int64"                  : "int64",
    "enumList"               : "int32",
    "string"                 : "string",
    "capability"             : "Capability",
    "multiResolutionStreamConfigurationMap" : "MultiResolutionStreamConfigurations",
    "deviceStateSensorOrientationMap"  : "DeviceStateSensorOrientationMap",
    "dynamicRangeProfiles"   : "DynamicRangeProfiles",
    "colorSpaceProfiles"     : "ColorSpaceProfiles",
    "versionCode"            : "int32",
    "sharedSessionConfiguration"  : "SharedSessionConfiguration",
  }

  if typeName not in typename_to_protobuftype:
    print("  ERROR: Could not find protocol buffer type for {%s} type {%s} typedef {%s}" % \
          (entry.name, entry.type, entry.typedef), file=sys.stderr)

  proto_type = typename_to_protobuftype[typeName]

  prefix = "optional"
  if entry.container == 'array':
    has_variable_size = False
    for size in entry.container_sizes:
      try:
        size_int = int(size)
      except ValueError:
        has_variable_size = True

    if has_variable_size:
      prefix = "repeated"

  return "%s %s" %(prefix, proto_type)


def protobuf_name(entry):
  """
  Return the protocol buffer field name for input metadata entry

  Returns:
    A string. Ex: "android_colorCorrection_availableAberrationModes"
  """
  return entry.name.replace(".", "_")

def has_descendants_with_enums(node):
  """
  Determine whether or not the current node is or has any descendants with an
  Enum node.

  Args:
    node: a Node instance

  Returns:
    True if it finds an Enum node in the subtree, False otherwise
  """
  return bool(node.find_first(lambda x: isinstance(x, metadata_model.Enum)))

def get_children_by_throwing_away_kind(node, member='entries'):
  """
  Get the children of this node by compressing the subtree together by removing
  the kind and then combining any children nodes with the same name together.

  Args:
    node: An instance of Section, InnerNamespace, or Kind

  Returns:
    An iterable over the combined children of the subtree of node,
    as if the Kinds never existed.

  Remarks:
    Not recursive. Call this function repeatedly on each child.
  """

  if isinstance(node, metadata_model.Section):
    # Note that this makes jump from Section to Kind,
    # skipping the Kind entirely in the tree.
    node_to_combine = node.combine_kinds_into_single_node()
  else:
    node_to_combine = node

  combined_kind = node_to_combine.combine_children_by_name()

  return (i for i in getattr(combined_kind, member))

def get_children_by_filtering_kind(section, kind_name, member='entries'):
  """
  Takes a section and yields the children of the merged kind under this section.

  Args:
    section: An instance of Section
    kind_name: A name of the kind, i.e. 'dynamic' or 'static' or 'controls'

  Returns:
    An iterable over the children of the specified merged kind.
  """

  matched_kind = next((i for i in section.merged_kinds if i.name == kind_name), None)

  if matched_kind:
    return getattr(matched_kind, member)
  else:
    return ()

##
## Filters
##

# abcDef.xyz -> ABC_DEF_XYZ
def csym(name):
  """
  Convert an entry name string into an uppercase C symbol.

  Returns:
    A string

  Example:
    csym('abcDef.xyz') == 'ABC_DEF_XYZ'
  """
  newstr = name
  newstr = "".join([i.isupper() and ("_" + i) or i for i in newstr]).upper()
  newstr = newstr.replace(".", "_")
  return newstr

# abcDef.xyz -> abc_def_xyz
def csyml(name):
  """
  Convert an entry name string into a lowercase C symbol.

  Returns:
    A string

  Example:
    csyml('abcDef.xyz') == 'abc_def_xyz'
  """
  return csym(name).lower()

# pad with spaces to make string len == size. add new line if too big
def ljust(size, indent=4):
  """
  Creates a function that given a string will pad it with spaces to make
  the string length == size. Adds a new line if the string was too big.

  Args:
    size: an integer representing how much spacing should be added
    indent: an integer representing the initial indendation level

  Returns:
    A function that takes a string and returns a string.

  Example:
    ljust(8)("hello") == 'hello   '

  Remarks:
    Deprecated. Use pad instead since it works for non-first items in a
    Mako template.
  """
  def inner(what):
    newstr = what.ljust(size)
    if len(newstr) > size:
      return what + "\n" + "".ljust(indent + size)
    else:
      return newstr
  return inner

def _find_new_line():

  if _context_buf is None:
    raise ValueError("Context buffer was not set")

  buf = _context_buf
  x = -1 # since the first read is always ''
  cur_pos = buf.tell()
  while buf.tell() > 0 and buf.read(1) != '\n':
    buf.seek(cur_pos - x)
    x = x + 1

  buf.seek(cur_pos)

  return int(x)

# Pad the string until the buffer reaches the desired column.
# If string is too long, insert a new line with 'col' spaces instead
def pad(col):
  """
  Create a function that given a string will pad it to the specified column col.
  If the string overflows the column, put the string on a new line and pad it.

  Args:
    col: an integer specifying the column number

  Returns:
    A function that given a string will produce a padded string.

  Example:
    pad(8)("hello") == 'hello   '

  Remarks:
    This keeps track of the line written by Mako so far, so it will always
    align to the column number correctly.
  """
  def inner(what):
    wut = int(col)
    current_col = _find_new_line()

    if len(what) > wut - current_col:
      return what + "\n".ljust(col)
    else:
      return what.ljust(wut - current_col)
  return inner

# int32 -> TYPE_INT32, byte -> TYPE_BYTE, etc. note that enum -> TYPE_INT32
def ctype_enum(what):
  """
  Generate a camera_metadata_type_t symbol from a type string.

  Args:
    what: a type string

  Returns:
    A string representing the camera_metadata_type_t

  Example:
    ctype_enum('int32') == 'TYPE_INT32'
    ctype_enum('int64') == 'TYPE_INT64'
    ctype_enum('float') == 'TYPE_FLOAT'

  Remarks:
    An enum is coerced to a byte since the rest of the camera_metadata
    code doesn't support enums directly yet.
  """
  return 'TYPE_%s' %(what.upper())


# Calculate a java type name from an entry with a Typedef node
def _jtypedef_type(entry):
  typedef = entry.typedef
  additional = ''

  # Hacky way to deal with arrays. Assume that if we have
  # size 'Constant x N' the Constant is part of the Typedef size.
  # So something sized just 'Constant', 'Constant1 x Constant2', etc
  # is not treated as a real java array.
  if entry.container == 'array':
    has_variable_size = False
    for size in entry.container_sizes:
      try:
        size_int = int(size)
      except ValueError:
        has_variable_size = True

    if has_variable_size:
      additional = '[]'

  try:
    name = typedef.languages['java']

    return "%s%s" %(name, additional)
  except KeyError:
    return None

# Box if primitive. Otherwise leave unboxed.
def _jtype_box(type_name):
  mapping = {
    'boolean': 'Boolean',
    'byte': 'Byte',
    'int': 'Integer',
    'float': 'Float',
    'double': 'Double',
    'long': 'Long'
  }

  return mapping.get(type_name, type_name)

def jtype_unboxed(entry):
  """
  Calculate the Java type from an entry type string, to be used whenever we
  need the regular type in Java. It's not boxed, so it can't be used as a
  generic type argument when the entry type happens to resolve to a primitive.

  Remarks:
    Since Java generics cannot be instantiated with primitives, this version
    is not applicable in that case. Use jtype_boxed instead for that.

  Returns:
    The string representing the Java type.
  """
  if not isinstance(entry, metadata_model.Entry):
    raise ValueError("Expected entry to be an instance of Entry")

  metadata_type = entry.type

  java_type = None

  if entry.typedef:
    typedef_name = _jtypedef_type(entry)
    if typedef_name:
      java_type = typedef_name # already takes into account arrays

  if not java_type:
    if not java_type and entry.enum and metadata_type == 'byte':
      # Always map byte enums to Java ints, unless there's a typedef override
      base_type = 'int'

    else:
      mapping = {
        'int32': 'int',
        'int64': 'long',
        'float': 'float',
        'double': 'double',
        'byte': 'byte',
        'rational': 'Rational'
      }

      base_type = mapping[metadata_type]

    # Convert to array (enums, basic types)
    if entry.container == 'array':
      additional = '[]'
    else:
      additional = ''

    java_type = '%s%s' %(base_type, additional)

  # Now box this sucker.
  return java_type

def jtype_boxed(entry):
  """
  Calculate the Java type from an entry type string, to be used as a generic
  type argument in Java. The type is guaranteed to inherit from Object.

  It will only box when absolutely necessary, i.e. int -> Integer[], but
  int[] -> int[].

  Remarks:
    Since Java generics cannot be instantiated with primitives, this version
    will use boxed types when absolutely required.

  Returns:
    The string representing the boxed Java type.
  """
  unboxed_type = jtype_unboxed(entry)
  return _jtype_box(unboxed_type)

def _is_jtype_generic(entry):
  """
  Determine whether or not the Java type represented by the entry type
  string and/or typedef is a Java generic.

  For example, "Range<Integer>" would be considered a generic, whereas
  a "MeteringRectangle" or a plain "Integer" would not be considered a generic.

  Args:
    entry: An instance of an Entry node

  Returns:
    True if it's a java generic, False otherwise.
  """
  if entry.typedef:
    local_typedef = _jtypedef_type(entry)
    if local_typedef:
      match = re.search(r'<.*>', local_typedef)
      return bool(match)
  return False

def _jtype_primitive(what):
  """
  Calculate the Java type from an entry type string.

  Remarks:
    Makes a special exception for Rational, since it's a primitive in terms of
    the C-library camera_metadata type system.

  Returns:
    The string representing the primitive type
  """
  mapping = {
    'int32': 'int',
    'int64': 'long',
    'float': 'float',
    'double': 'double',
    'byte': 'byte',
    'rational': 'Rational'
  }

  try:
    return mapping[what]
  except KeyError as e:
    raise ValueError("Can't map '%s' to a primitive, not supported" %what)

def jclass(entry):
  """
  Calculate the java Class reference string for an entry.

  Args:
    entry: an Entry node

  Example:
    <entry name="some_int" type="int32"/>
    <entry name="some_int_array" type="int32" container='array'/>

    jclass(some_int) == 'int.class'
    jclass(some_int_array) == 'int[].class'

  Returns:
    The ClassName.class string
  """

  return "%s.class" %jtype_unboxed(entry)

def jkey_type_token(entry):
  """
  Calculate the java type token compatible with a Key constructor.
  This will be the Java Class<T> for non-generic classes, and a
  TypeReference<T> for generic classes.

  Args:
    entry: An entry node

  Returns:
    The ClassName.class string, or 'new TypeReference<ClassName>() {{ }}' string
  """
  if _is_jtype_generic(entry):
    return "new TypeReference<%s>() {{ }}" %(jtype_boxed(entry))
  else:
    return jclass(entry)

def jidentifier(what):
  """
  Convert the input string into a valid Java identifier.

  Args:
    what: any identifier string

  Returns:
    String with added underscores if necessary.
  """
  if re.match("\d", what):
    return "_%s" %what
  else:
    return what

def enum_calculate_value_string(enum_value):
  """
  Calculate the value of the enum, even if it does not have one explicitly
  defined.

  This looks back for the first enum value that has a predefined value and then
  applies addition until we get the right value, using C-enum semantics.

  Args:
    enum_value: an EnumValue node with a valid Enum parent

  Example:
    <enum>
      <value>X</value>
      <value id="5">Y</value>
      <value>Z</value>
    </enum>

    enum_calculate_value_string(X) == '0'
    enum_calculate_Value_string(Y) == '5'
    enum_calculate_value_string(Z) == '6'

  Returns:
    String that represents the enum value as an integer literal.
  """

  enum_value_siblings = list(enum_value.parent.values)
  this_index = enum_value_siblings.index(enum_value)

  def is_hex_string(instr):
    return bool(re.match('0x[a-f0-9]+$', instr, re.IGNORECASE))

  base_value = 0
  base_offset = 0
  emit_as_hex = False

  this_id = enum_value_siblings[this_index].id
  while this_index != 0 and not this_id:
    this_index -= 1
    base_offset += 1
    this_id = enum_value_siblings[this_index].id

  if this_id:
    base_value = int(this_id, 0)  # guess base
    emit_as_hex = is_hex_string(this_id)

  if emit_as_hex:
    return "0x%X" %(base_value + base_offset)
  else:
    return "%d" %(base_value + base_offset)

def enumerate_with_last(iterable):
  """
  Enumerate a sequence of iterable, while knowing if this element is the last in
  the sequence or not.

  Args:
    iterable: an Iterable of some sequence

  Yields:
    (element, bool) where the bool is True iff the element is last in the seq.
  """
  it = (i for i in iterable)

  try:
    first = next(it)  # OK: raises exception if it is empty
  except StopIteration:
    return

  second = first  # for when we have only 1 element in iterable

  try:
    while True:
      second = next(it)
      # more elements remaining.
      yield (first, False)
      first = second
  except StopIteration:
    # last element. no more elements left
    yield (second, True)

def pascal_case(what):
  """
  Convert the first letter of a string to uppercase, to make the identifier
  conform to PascalCase.

  If there are dots, remove the dots, and capitalize the letter following
  where the dot was. Letters that weren't following dots are left unchanged,
  except for the first letter of the string (which is made upper-case).

  Args:
    what: a string representing some identifier

  Returns:
    String with first letter capitalized

  Example:
    pascal_case("helloWorld") == "HelloWorld"
    pascal_case("foo") == "Foo"
    pascal_case("hello.world") = "HelloWorld"
    pascal_case("fooBar.fooBar") = "FooBarFooBar"
  """
  return "".join([s[0:1].upper() + s[1:] for s in what.split('.')])

def jkey_identifier(what):
  """
  Return a Java identifier from a property name.

  Args:
    what: a string representing a property name.

  Returns:
    Java identifier corresponding to the property name. May need to be
    prepended with the appropriate Java class name by the caller of this
    function. Note that the outer namespace is stripped from the property
    name.

  Example:
    jkey_identifier("android.lens.facing") == "LENS_FACING"
  """
  return csym(what[what.find('.') + 1:])

def jenum_value(enum_entry, enum_value):
  """
  Calculate the Java name for an integer enum value

  Args:
    enum: An enum-typed Entry node
    value: An EnumValue node for the enum

  Returns:
    String representing the Java symbol
  """

  cname = csym(enum_entry.name)
  return cname[cname.find('_') + 1:] + '_' + enum_value.name

def generate_extra_javadoc_detail(entry):
  """
  Returns a function to add extra details for an entry into a string for inclusion into
  javadoc. Adds information about units, the list of enum values for this key, and the valid
  range.
  """
  def inner(text):
    if entry.units and not (entry.typedef and entry.typedef.name == 'string'):
      text += '\n\n<b>Units</b>: %s\n' % (dedent(entry.units))
    if entry.enum and not (entry.typedef and entry.typedef.languages.get('java')):
      text += '\n\n<b>Possible values:</b>\n<ul>\n'
      for value in entry.enum.values:
        if not value.hidden and (value.aconfig_flag == entry.aconfig_flag):
          text += '  <li>{@link #%s %s}</li>\n' % ( jenum_value(entry, value ), value.name )
      text += '</ul>\n'
    if entry.range:
      if entry.enum and not (entry.typedef and entry.typedef.languages.get('java')):
        text += '\n\n<b>Available values for this device:</b><br>\n'
      else:
        text += '\n\n<b>Range of valid values:</b><br>\n'
      text += '%s\n' % (dedent(entry.range))
    if entry.hwlevel != 'legacy': # covers any of (None, 'limited', 'full')
      text += '\n\n<b>Optional</b> - The value for this key may be {@code null} on some devices.\n'
    if entry.hwlevel == 'full':
      text += \
        '\n<b>Full capability</b> - \n' + \
        'Present on all camera devices that report being {@link CameraCharacteristics#INFO_SUPPORTED_HARDWARE_LEVEL_FULL HARDWARE_LEVEL_FULL} devices in the\n' + \
        'android.info.supportedHardwareLevel key\n'
    if entry.hwlevel == 'limited':
      text += \
        '\n<b>Limited capability</b> - \n' + \
        'Present on all camera devices that report being at least {@link CameraCharacteristics#INFO_SUPPORTED_HARDWARE_LEVEL_LIMITED HARDWARE_LEVEL_LIMITED} devices in the\n' + \
        'android.info.supportedHardwareLevel key\n'
    if entry.hwlevel == 'legacy':
      text += "\nThis key is available on all devices."
    if entry.permission_needed == "true":
      text += "\n\n<b>Permission {@link android.Manifest.permission#CAMERA} is needed to access this property</b>\n\n"

    return text
  return inner


def javadoc(metadata, indent = 4):
  """
  Returns a function to format a markdown syntax text block as a
  javadoc comment section, given a set of metadata

  Args:
    metadata: A Metadata instance, representing the top-level root
      of the metadata for cross-referencing
    indent: baseline level of indentation for javadoc block
  Returns:
    A function that transforms a String text block as follows:
    - Indent and * for insertion into a Javadoc comment block
    - Trailing whitespace removed
    - Entire body rendered via markdown to generate HTML
    - All tag names converted to appropriate Javadoc {@link} with @see
      for each tag

  Example:
    "This is a comment for Javadoc\n" +
    "     with multiple lines, that should be   \n" +
    "     formatted better\n" +
    "\n" +
    "    That covers multiple lines as well\n"
    "    And references android.control.mode\n"

    transforms to
    "    * <p>This is a comment for Javadoc\n" +
    "    * with multiple lines, that should be\n" +
    "    * formatted better</p>\n" +
    "    * <p>That covers multiple lines as well</p>\n" +
    "    * and references {@link CaptureRequest#CONTROL_MODE android.control.mode}\n" +
    "    *\n" +
    "    * @see CaptureRequest#CONTROL_MODE\n"
  """
  def javadoc_formatter(text):
    comment_prefix = " " * indent + " * "

    # render with markdown => HTML
    javatext = md(text, JAVADOC_IMAGE_SRC_METADATA)

    # Identity transform for javadoc links
    def javadoc_link_filter(target, target_ndk, shortname):
      return '{@link %s %s}' % (target, shortname)

    javatext = filter_links(javatext, javadoc_link_filter)

    # Crossref tag names
    kind_mapping = {
        'static': 'CameraCharacteristics',
        'dynamic': 'CaptureResult',
        'controls': 'CaptureRequest' }

    # Convert metadata entry "android.x.y.z" to form
    # "{@link CaptureRequest#X_Y_Z android.x.y.z}"
    def javadoc_crossref_filter(node):
      if node.applied_visibility in ('public', 'java_public', 'fwk_java_public', 'fwk_public',\
                                     'fwk_system_public'):
        return '{@link %s#%s %s}' % (kind_mapping[node.kind],
                                     jkey_identifier(node.name),
                                     node.name)
      else:
        return node.name

    # For each public tag "android.x.y.z" referenced, add a
    # "@see CaptureRequest#X_Y_Z"
    def javadoc_crossref_see_filter(node_set):
      node_set = (x for x in node_set if x.applied_visibility in \
                  ('public', 'java_public', 'fwk_java_public', 'fwk_public', 'fwk_system_public'))

      text = '\n'
      for node in node_set:
        text = text + '\n@see %s#%s' % (kind_mapping[node.kind],
                                      jkey_identifier(node.name))

      return text if text != '\n' else ''

    javatext = filter_tags(javatext, metadata, javadoc_crossref_filter, javadoc_crossref_see_filter)

    def line_filter(line):
      # Indent each line
      # Add ' * ' to it for stylistic reasons
      # Strip right side of trailing whitespace
      return (comment_prefix + line).rstrip()

    # Process each line with above filter
    javatext = "\n".join(line_filter(i) for i in javatext.split("\n")) + "\n"

    return javatext

  return javadoc_formatter

def ndkdoc(metadata, indent = 4):
  """
  Returns a function to format a markdown syntax text block as a
  NDK camera API C/C++ comment section, given a set of metadata

  Args:
    metadata: A Metadata instance, representing the top-level root
      of the metadata for cross-referencing
    indent: baseline level of indentation for comment block
  Returns:
    A function that transforms a String text block as follows:
    - Indent and * for insertion into a comment block
    - Trailing whitespace removed
    - Entire body rendered via markdown
    - All tag names converted to appropriate NDK tag name for each tag

  Example:
    "This is a comment for NDK\n" +
    "     with multiple lines, that should be   \n" +
    "     formatted better\n" +
    "\n" +
    "    That covers multiple lines as well\n"
    "    And references android.control.mode\n"

    transforms to
    "    * This is a comment for NDK\n" +
    "    * with multiple lines, that should be\n" +
    "    * formatted better\n" +
    "    * That covers multiple lines as well\n" +
    "    * and references ACAMERA_CONTROL_MODE\n" +
    "    *\n" +
    "    * @see ACAMERA_CONTROL_MODE\n"
  """
  def ndkdoc_formatter(text):
    # render with markdown => HTML
    # Turn off the table plugin since doxygen doesn't recognize generated <thead> <tbody> tags
    ndktext = md(text, NDKDOC_IMAGE_SRC_METADATA, False)

    # Simple transform for ndk doc links
    def ndkdoc_link_filter(target, target_ndk, shortname):
      if target_ndk is not None:
        return '{@link %s %s}' % (target_ndk, shortname)

      # Create HTML link to Javadoc
      if shortname == '':
        lastdot = target.rfind('.')
        if lastdot == -1:
          shortname = target
        else:
          shortname = target[lastdot + 1:]

      target = target.replace('.','/')
      if target.find('#') != -1:
        target = target.replace('#','.html#')
      else:
        target = target + '.html'

      # Work around html links with inner classes.
      target = target.replace('CaptureRequest/Builder', 'CaptureRequest.Builder')
      target = target.replace('Build/VERSION', 'Build.VERSION')

      return '<a href="https://developer.android.com/reference/%s">%s</a>' % (target, shortname)

    ndktext = filter_links(ndktext, ndkdoc_link_filter)

    # Convert metadata entry "android.x.y.z" to form
    # NDK tag format of "ACAMERA_X_Y_Z"
    def ndkdoc_crossref_filter(node):
      if node.applied_ndk_visible == 'true':
        return csym(ndk(node.name))
      else:
        return node.name

    # For each public tag "android.x.y.z" referenced, add a
    # "@see ACAMERA_X_Y_Z"
    def ndkdoc_crossref_see_filter(node_set):
      node_set = (x for x in node_set if x.applied_ndk_visible == 'true')

      text = '\n'
      for node in node_set:
        text = text + '\n@see %s' % (csym(ndk(node.name)))

      return text if text != '\n' else ''

    ndktext = filter_tags(ndktext, metadata, ndkdoc_crossref_filter, ndkdoc_crossref_see_filter)

    ndktext = ndk_replace_tag_wildcards(ndktext, metadata)

    comment_prefix = " " * indent + " * ";

    def line_filter(line):
      # Indent each line
      # Add ' * ' to it for stylistic reasons
      # Strip right side of trailing whitespace
      return (comment_prefix + line).rstrip()

    # Process each line with above filter
    ndktext = "\n".join(line_filter(i) for i in ndktext.split("\n")) + "\n"

    return ndktext

  return ndkdoc_formatter

def hidldoc(metadata, indent = 4):
  """
  Returns a function to format a markdown syntax text block as a
  HIDL camera HAL module C/C++ comment section, given a set of metadata

  Args:
    metadata: A Metadata instance, representing the top-level root
      of the metadata for cross-referencing
    indent: baseline level of indentation for comment block
  Returns:
    A function that transforms a String text block as follows:
    - Indent and * for insertion into a comment block
    - Trailing whitespace removed
    - Entire body rendered via markdown
    - All tag names converted to appropriate HIDL tag name for each tag

  Example:
    "This is a comment for NDK\n" +
    "     with multiple lines, that should be   \n" +
    "     formatted better\n" +
    "\n" +
    "    That covers multiple lines as well\n"
    "    And references android.control.mode\n"

    transforms to
    "    * This is a comment for NDK\n" +
    "    * with multiple lines, that should be\n" +
    "    * formatted better\n" +
    "    * That covers multiple lines as well\n" +
    "    * and references ANDROID_CONTROL_MODE\n" +
    "    *\n" +
    "    * @see ANDROID_CONTROL_MODE\n"
  """
  def hidldoc_formatter(text):
    # render with markdown => HTML
    # Turn off the table plugin since doxygen doesn't recognize generated <thead> <tbody> tags
    hidltext = md(text, NDKDOC_IMAGE_SRC_METADATA, False)

    # Simple transform for hidl doc links
    def hidldoc_link_filter(target, target_ndk, shortname):
      if target_ndk is not None:
        return '{@link %s %s}' % (target_ndk, shortname)

      # Create HTML link to Javadoc
      if shortname == '':
        lastdot = target.rfind('.')
        if lastdot == -1:
          shortname = target
        else:
          shortname = target[lastdot + 1:]

      target = target.replace('.','/')
      if target.find('#') != -1:
        target = target.replace('#','.html#')
      else:
        target = target + '.html'

      return '<a href="https://developer.android.com/reference/%s">%s</a>' % (target, shortname)

    hidltext = filter_links(hidltext, hidldoc_link_filter)

    # Convert metadata entry "android.x.y.z" to form
    # HIDL tag format of "ANDROID_X_Y_Z"
    def hidldoc_crossref_filter(node):
      return csym(node.name)

    # For each public tag "android.x.y.z" referenced, add a
    # "@see ANDROID_X_Y_Z"
    def hidldoc_crossref_see_filter(node_set):
      text = '\n'
      for node in node_set:
        text = text + '\n@see %s' % (csym(node.name))

      return text if text != '\n' else ''

    hidltext = filter_tags(hidltext, metadata, hidldoc_crossref_filter, hidldoc_crossref_see_filter)

    comment_prefix = " " * indent + " * ";

    def line_filter(line):
      # Indent each line
      # Add ' * ' to it for stylistic reasons
      # Strip right side of trailing whitespace
      return (comment_prefix + line).rstrip()

    # Process each line with above filter
    hidltext = "\n".join(line_filter(i) for i in hidltext.split("\n")) + "\n"

    return hidltext

  return hidldoc_formatter

def dedent(text):
  """
  Remove all common indentation from every line but the 0th.
  This will avoid getting <code> blocks when rendering text via markdown.
  Ignoring the 0th line will also allow the 0th line not to be aligned.

  Args:
    text: A string of text to dedent.

  Returns:
    String dedented by above rules.

  For example:
    assertEqual("bar\nline1\nline2",   dedent("bar\n  line1\n  line2"))
    assertEqual("bar\nline1\nline2",   dedent(" bar\n  line1\n  line2"))
    assertEqual("bar\n  line1\nline2", dedent(" bar\n    line1\n  line2"))
  """
  text = textwrap.dedent(text)
  text_lines = text.split('\n')
  text_not_first = "\n".join(text_lines[1:])
  text_not_first = textwrap.dedent(text_not_first)
  text = text_lines[0] + "\n" + text_not_first

  return text

def md(text, img_src_prefix="", table_ext=True):
    """
    Run text through markdown to produce HTML.

    This also removes all common indentation from every line but the 0th.
    This will avoid getting <code> blocks in markdown.
    Ignoring the 0th line will also allow the 0th line not to be aligned.

    Args:
      text: A markdown-syntax using block of text to format.
      img_src_prefix: An optional string to prepend to each <img src="target"/>

    Returns:
      String rendered by markdown and other rules applied (see above).

    For example, this avoids the following situation:

      <!-- Input -->

      <!--- can't use dedent directly since 'foo' has no indent -->
      <notes>foo
          bar
          bar
      </notes>

      <!-- Bad Output -- >
      <!-- if no dedent is done generated code looks like -->
      <p>foo
        <code><pre>
          bar
          bar</pre></code>
      </p>

    Instead we get the more natural expected result:

      <!-- Good Output -->
      <p>foo
      bar
      bar</p>

    """
    text = dedent(text)

    # full list of extensions at http://pythonhosted.org/Markdown/extensions/
    md_extensions = ['tables'] if table_ext else []# make <table> with ASCII |_| tables
    # render with markdown
    text = markdown.markdown(text, extensions=md_extensions)

    # prepend a prefix to each <img src="foo"> -> <img src="${prefix}foo">
    text = re.sub(r'src="([^"]*)"', 'src="' + img_src_prefix + r'\1"', text)
    return text

def filter_tags(text, metadata, filter_function, summary_function = None):
    """
    Find all references to tags in the form outer_namespace.xxx.yyy[.zzz] in
    the provided text, and pass them through filter_function and summary_function.

    Used to linkify entry names in HMTL, javadoc output.

    Args:
      text: A string representing a block of text destined for output
      metadata: A Metadata instance, the root of the metadata properties tree
      filter_function: A Node->string function to apply to each node
        when found in text; the string returned replaces the tag name in text.
      summary_function: A Node list->string function that is provided the list of
        unique tag nodes found in text, and which must return a string that is
        then appended to the end of the text. The list is sorted alphabetically
        by node name.
    """

    tag_set = set()
    def name_match(name):
      return lambda node: node.name == name

    # Match outer_namespace.x.y or outer_namespace.x.y.z, making sure
    # to grab .z and not just outer_namespace.x.y.  (sloppy, but since we
    # check for validity, a few false positives don't hurt).
    # Try to ignore items of the form {@link <outer_namespace>...
    for outer_namespace in metadata.outer_namespaces:

      tag_match = r"(?<!\{@link\s)" + outer_namespace.name + \
        r"\.([a-zA-Z0-9\n]+)\.([a-zA-Z0-9\n]+)(\.[a-zA-Z0-9\n]+)?([/]?)"

      def filter_sub(match):
        whole_match = match.group(0)
        section1 = match.group(1)
        section2 = match.group(2)
        section3 = match.group(3)
        end_slash = match.group(4)

        # Don't linkify things ending in slash (urls, for example)
        if end_slash:
          return whole_match

        candidate = ""

        # First try a two-level match
        candidate2 = "%s.%s.%s" % (outer_namespace.name, section1, section2)
        got_two_level = False

        node = metadata.find_first(name_match(candidate2.replace('\n','')))
        if not node and '\n' in section2:
          # Linefeeds are ambiguous - was the intent to add a space,
          # or continue a lengthy name? Try the former now.
          candidate2b = "%s.%s.%s" % (outer_namespace.name, section1, section2[:section2.find('\n')])
          node = metadata.find_first(name_match(candidate2b))
          if node:
            candidate2 = candidate2b

        if node:
          # Have two-level match
          got_two_level = True
          candidate = candidate2
        elif section3:
          # Try three-level match
          candidate3 = "%s%s" % (candidate2, section3)
          node = metadata.find_first(name_match(candidate3.replace('\n','')))

          if not node and '\n' in section3:
            # Linefeeds are ambiguous - was the intent to add a space,
            # or continue a lengthy name? Try the former now.
            candidate3b = "%s%s" % (candidate2, section3[:section3.find('\n')])
            node = metadata.find_first(name_match(candidate3b))
            if node:
              candidate3 = candidate3b

          if node:
            # Have 3-level match
            candidate = candidate3

        # Replace match with crossref or complain if a likely match couldn't be matched

        if node:
          tag_set.add(node)
          return whole_match.replace(candidate,filter_function(node))
        else:
          print("  WARNING: Could not crossref likely reference {%s}" % (match.group(0)),
                file=sys.stderr)
          return whole_match

      text = re.sub(tag_match, filter_sub, text)

    if summary_function is not None:
      return text + summary_function(sorted(tag_set, key=lambda x: x.name))
    else:
      return text

def ndk_replace_tag_wildcards(text, metadata):
    """
    Find all references to tags in the form android.xxx.* or android.xxx.yyy.*
    in the provided text, and replace them by NDK format of "ACAMERA_XXX_*" or
    "ACAMERA_XXX_YYY_*"

    Args:
      text: A string representing a block of text destined for output
      metadata: A Metadata instance, the root of the metadata properties tree
    """
    tag_match = r"android\.([a-zA-Z0-9\n]+)\.\*"
    tag_match_2 = r"android\.([a-zA-Z0-9\n]+)\.([a-zA-Z0-9\n]+)\*"

    def filter_sub(match):
      return "ACAMERA_" + match.group(1).upper() + "_*"
    def filter_sub_2(match):
      return "ACAMERA_" + match.group(1).upper() + match.group(2).upper() + "_*"

    text = re.sub(tag_match, filter_sub, text)
    text = re.sub(tag_match_2, filter_sub_2, text)
    return text

def filter_links(text, filter_function, summary_function = None):
    """
    Find all references to tags in the form {@link xxx#yyy [zzz]} in the
    provided text, and pass them through filter_function and
    summary_function.

    Used to linkify documentation cross-references in HMTL, javadoc output.

    Args:
      text: A string representing a block of text destined for output
      metadata: A Metadata instance, the root of the metadata properties tree
      filter_function: A (string, string)->string function to apply to each 'xxx#yyy',
        zzz pair when found in text; the string returned replaces the tag name in text.
      summary_function: A string list->string function that is provided the list of
        unique targets found in text, and which must return a string that is
        then appended to the end of the text. The list is sorted alphabetically
        by node name.

    """

    target_set = set()
    def name_match(name):
      return lambda node: node.name == name

    tag_match = r"\{@link\s+([^\s\}\|]+)(?:\|([^\s\}]+))*([^\}]*)\}"

    def filter_sub(match):
      whole_match = match.group(0)
      target = match.group(1)
      target_ndk = match.group(2)
      shortname = match.group(3).strip()

      #print("Found link '%s' ndk '%s' as '%s' -> '%s'" % (target, target_ndk, shortname, filter_function(target, target_ndk, shortname)))

      # Replace match with crossref
      target_set.add(target)
      return filter_function(target, target_ndk, shortname)

    text = re.sub(tag_match, filter_sub, text)

    if summary_function is not None:
      return text + summary_function(sorted(target_set))
    else:
      return text

def any_visible(section, kind_name, visibilities):
  """
  Determine if entries in this section have an applied visibility that's in
  the list of given visibilities.

  Args:
    section: A section of metadata
    kind_name: A name of the kind, i.e. 'dynamic' or 'static' or 'controls'
    visibilities: An iterable of visibilities to match against

  Returns:
    True if the section has any entries with any of the given visibilities. False otherwise.
  """

  for inner_namespace in get_children_by_filtering_kind(section, kind_name,
                                                        'namespaces'):
    if any(filter_visibility(inner_namespace.merged_entries, visibilities)):
      return True

  return any(filter_visibility(get_children_by_filtering_kind(section, kind_name,
                                                              'merged_entries'),
                               visibilities))

def filter_visibility(entries, visibilities):
  """
  Remove entries whose applied visibility is not in the supplied visibilities.

  Args:
    entries: An iterable of Entry nodes
    visibilities: An iterable of visibilities to filter against

  Yields:
    An iterable of Entry nodes
  """
  return (e for e in entries if e.applied_visibility in visibilities)

def is_not_hal_visible(e):
  """
  Determine that the entry being passed in is not visible to HAL.

  Args:
    e: An entry node

  Returns:
    True if the entry is not visible to HAL
  """
  return (e.visibility == 'fwk_only' or
          e.visibility == 'fwk_java_public' or
          e.visibility == 'fwk_public' or
          e.visibility == 'fwk_system_public' or
          e.visibility == 'fwk_ndk_public' or
          e.visibility == 'extension')

def remove_hal_non_visible(entries):
  """
  Filter the given entries by removing those that are not HAL visible:
  synthetic, fwk_only, extension, fwk_java_public, fwk_system_public, fwk_ndk_public,
  or fwk_public.

  Args:
    entries: An iterable of Entry nodes

  Yields:
    An iterable of Entry nodes
  """
  return (e for e in entries if not (e.synthetic or e.visibility == 'extension_passthrough' or
                                     is_not_hal_visible(e)))

def remove_ndk_non_visible(entries):
  """
  Filter the given entries by removing those that are not NDK visible:
  synthetic, fwk_only, extension, or fwk_java_public.

  Args:
    entries: An iterable of Entry nodes

  Yields:
    An iterable of Entry nodes
  """
  return (e for e in entries if not (e.synthetic or e.visibility == 'fwk_only'
                                     or e.visibility == 'fwk_java_public' or
                                     e.visibility == 'extension'))

"""
  Return the vndk version for a given hal minor version. The major version is assumed to be 3

  Args:
    hal_minor_version : minor version to retrieve the vndk version for

  Yields:
    int representing the vndk version
  """
def get_vndk_version(hal_minor_version):
  if hal_minor_version <= FRAMEWORK_CAMERA_VNDK_HAL_MINOR_VERSION:
    return 0
  return hal_minor_version - FRAMEWORK_CAMERA_VNDK_HAL_MINOR_VERSION \
        + FRAMEWORK_CAMERA_VNDK_STARTING_VERSION

"""
  Returns an api level -> dict of metadata tags corresponding to the api level

  Args:
    sections : metadata sections to create the mapping for
    metadata: the metadata structure to be used to create the mapping
    kind : kind of entries to create a mapping for : 'static' or 'dynamic'

  Yields:
    A dictionary mapping api level to a dictionary of metadata tags for the particular key (api level)
  """
def get_api_level_to_keys(sections, metadata, kind):
  api_level_to_keys = {}
  for sec in sections:
    for idx,entry in enumerate(remove_synthetic(find_unique_entries(sec))):
      if entry._hal_minor_version > FRAMEWORK_CAMERA_VNDK_HAL_MINOR_VERSION and \
          metadata.is_entry_this_kind(entry, kind):
        api_level = get_vndk_version(entry._hal_minor_version)
        try:
          api_level_to_keys[api_level].add(entry.name)
        except KeyError:
          api_level_to_keys[api_level] = {entry.name}
  #Insert the keys in sorted order since dicts in python (< 3.7, even OrderedDicts don't actually
  # sort keys)
  api_level_to_keys_ordered = OrderedDict()
  for api_level_ordered in sorted(api_level_to_keys.keys()):
    api_level_to_keys_ordered[api_level_ordered] = sorted(api_level_to_keys[api_level_ordered])
  return api_level_to_keys_ordered


def get_api_level_to_session_characteristic_keys(sections):
  """
  Returns a mapping of api_level -> list session characteristics tag keys where api_level
  is the level at which they became a part of getSessionCharacteristics call.

  Args:
    sections : metadata sections to create the mapping for

  Returns:
    A dictionary mapping api level to a list of metadata tags.
  """
  api_level_to_keys = defaultdict(list)
  for sec in sections:
    for entry in remove_synthetic(find_unique_entries(sec)):
      if entry.session_characteristics_key_since is None:
        continue

      api_level = entry.session_characteristics_key_since
      api_level_to_keys[api_level].append(entry.name)

  # sort dictionary on its key (api_level)
  api_level_to_keys = OrderedDict(sorted(api_level_to_keys.items(), key=itemgetter(0)))
  # sort the keys for each api_level
  for api_level, keys in api_level_to_keys.items():
    api_level_to_keys[api_level] = sorted(keys)

  return api_level_to_keys


def remove_synthetic(entries):
  """
  Filter the given entries by removing those that are synthetic.

  Args:
    entries: An iterable of Entry nodes

  Yields:
    An iterable of Entry nodes
  """
  return (e for e in entries if not e.synthetic)

def filter_added_in_hal_version(entries, hal_major_version, hal_minor_version):
  """
  Filter the given entries to those added in the given HIDL HAL version

  Args:
    entries: An iterable of Entry nodes
    hal_major_version: Major HIDL version to filter for
    hal_minor_version: Minor HIDL version to filter for

  Yields:
    An iterable of Entry nodes
  """
  return (e for e in entries if e.hal_major_version == hal_major_version and e.hal_minor_version == hal_minor_version)

def filter_has_enum_values_added_in_hal_version(entries, hal_major_version, hal_minor_version):
  """
  Filter the given entries to those that have a new enum value added in the given HIDL HAL version

  Args:
    entries: An iterable of Entry nodes
    hal_major_version: Major HIDL version to filter for
    hal_minor_version: Minor HIDL version to filter for

  Yields:
    An iterable of Entry nodes
  """
  return (e for e in entries if e.has_new_values_added_in_hal_version(hal_major_version, hal_minor_version))

def permission_needed_count(root):
  """
  Return the number entries that need camera permission.

  Args:
    root: a Metadata instance

  Returns:
    The number of entires that need camera permission.

  """
  ret = 0
  for sec in find_all_sections(root):
      ret += len(list(filter_has_permission_needed(remove_hal_non_visible(find_unique_entries(sec)))))

  return ret

def filter_has_permission_needed(entries):
  """
    Filter the given entries by removing those that don't need camera permission.

    Args:
      entries: An iterable of Entry nodes

    Yields:
      An iterable of Entry nodes
  """
  return (e for e in entries if e.permission_needed == 'true')

def filter_ndk_visible(entries):
  """
  Filter the given entries by removing those that are not NDK visible.

  Args:
    entries: An iterable of Entry nodes

  Yields:
    An iterable of Entry nodes
  """
  return (e for e in entries if e.applied_ndk_visible == 'true')

def wbr(text):
  """
  Insert word break hints for the browser in the form of <wbr> HTML tags.

  Word breaks are inserted inside an HTML node only, so the nodes themselves
  will not be changed. Attributes are also left unchanged.

  The following rules apply to insert word breaks:
  - For characters in [ '.', '/', '_' ]
  - For uppercase letters inside a multi-word X.Y.Z (at least 3 parts)

  Args:
    text: A string of text containing HTML content.

  Returns:
    A string with <wbr> inserted by the above rules.
  """
  SPLIT_CHARS_LIST = ['.', '_', '/']
  SPLIT_CHARS = r'([.|/|_/,]+)' # split by these characters
  CAP_LETTER_MIN = 3 # at least 3 components split by above chars, i.e. x.y.z
  def wbr_filter(text):
      new_txt = text

      # for johnyOrange.appleCider.redGuardian also insert wbr before the caps
      # => johny<wbr>Orange.apple<wbr>Cider.red<wbr>Guardian
      for words in text.split(" "):
        for char in SPLIT_CHARS_LIST:
          # match at least x.y.z, don't match x or x.y
          if len(words.split(char)) >= CAP_LETTER_MIN:
            new_word = re.sub(r"([a-z])([A-Z])", r"\1<wbr>\2", words)
            new_txt = new_txt.replace(words, new_word)

      # e.g. X/Y/Z -> X/<wbr>Y/<wbr>/Z. also for X.Y.Z, X_Y_Z.
      new_txt = re.sub(SPLIT_CHARS, r"\1<wbr>", new_txt)

      return new_txt

  # Do not mangle HTML when doing the replace by using BeatifulSoup
  # - Use the 'html.parser' to avoid inserting <html><body> when decoding
  soup = bs4.BeautifulSoup(text, features='html.parser')
  wbr_tag = lambda: soup.new_tag('wbr') # must generate new tag every time

  for navigable_string in soup.findAll(text=True):
      parent = navigable_string.parent

      # Insert each '$text<wbr>$foo' before the old '$text$foo'
      split_by_wbr_list = wbr_filter(navigable_string).split("<wbr>")
      for (split_string, last) in enumerate_with_last(split_by_wbr_list):
          navigable_string.insert_before(split_string)

          if not last:
            # Note that 'insert' will move existing tags to this spot
            # so make a new tag instead
            navigable_string.insert_before(wbr_tag())

      # Remove the old unmodified text
      navigable_string.extract()

  return soup.decode()

def copyright_year():
  return _copyright_year

def infer_copyright_year_from_source(src_file, default_copyright_year):
  """
  Opens src_file and tries to infer the copyright year from the file
  if it exists. Returns default_copyright_year if src_file is None, doesn't
  exist, or the copyright year cannot be parsed from the first 15 lines.

  Assumption:
    - Copyright text must be in the first 15 lines of the src_file.
      This should almost always be true.
  """
  if src_file is None:
    return default_copyright_year

  if not path.isfile(src_file):
    return default_copyright_year

  copyright_pattern = r"^.*Copyright \([Cc]\) (20\d\d) The Android Open Source Project$"
  num_max_lines = 15

  with open(src_file, "r") as f:
    for i, line in enumerate(f):
      if i >= num_max_lines:
        break

      years = re.findall(copyright_pattern, line.strip())
      if len(years) > 0:
        return years[0]

  return default_copyright_year

def enum():
  return _enum

def first_hal_minor_version(hal_major_version):
  return 2 if hal_major_version == 3 else 0

def find_all_sections_added_in_hal(root, hal_major_version, hal_minor_version):
  """
  Find all descendants that are Section or InnerNamespace instances, which
  were added in HIDL HAL version major.minor. The section is defined to be
  added in a HAL version iff the lowest HAL version number of its entries is
  that HAL version.

  Args:
    root: a Metadata instance
    hal_major/minor_version: HAL version numbers

  Returns:
    A list of Section/InnerNamespace instances

  Remarks:
    These are known as "sections" in the generated C code.
  """
  all_sections = find_all_sections(root)
  new_sections = []
  for section in all_sections:
    min_major_version = None
    min_minor_version = None
    for entry in remove_hal_non_visible(find_unique_entries(section)):
      min_major_version = (min_major_version or entry.hal_major_version)
      min_minor_version = (min_minor_version or entry.hal_minor_version)
      if entry.hal_major_version < min_major_version or \
          (entry.hal_major_version == min_major_version and entry.hal_minor_version < min_minor_version):
        min_minor_version = entry.hal_minor_version
        min_major_version = entry.hal_major_version
    if min_major_version == hal_major_version and min_minor_version == hal_minor_version:
      new_sections.append(section)
  return new_sections

def find_first_older_used_hal_version(section, hal_major_version, hal_minor_version):
  hal_version = (0, 0)
  for v in section.hal_versions:
    if (v[0] > hal_version[0] or (v[0] == hal_version[0] and v[1] > hal_version[1])) and \
        (v[0] < hal_major_version or (v[0] == hal_major_version and v[1] < hal_minor_version)):
      hal_version = v
  return hal_version

# Some exceptions need to be made regarding enum value identifiers in AIDL.
# Process them here.
def aidl_enum_value_name(name):
  if name == 'ANDROID_INFO_SUPPORTED_BUFFER_MANAGEMENT_VERSION_HIDL_DEVICE_3_5':
    name = 'ANDROID_INFO_SUPPORTED_BUFFER_MANAGEMENT_VERSION_AIDL_DEVICE'
  return name

def aidl_enum_values(entry):
  ignoreList = [
    'ANDROID_SCALER_AVAILABLE_RECOMMENDED_STREAM_CONFIGURATIONS_PUBLIC_END',
    'ANDROID_SCALER_AVAILABLE_RECOMMENDED_STREAM_CONFIGURATIONS_PUBLIC_END_3_8'
  ]
  return [
    val for val in entry.enum.values if '%s_%s'%(csym(entry.name), val.name) not in ignoreList
  ]

def java_symbol_for_aconfig_flag(flag_name):
  """
  Returns the java symbol for a give aconfig flag. This means converting
  snake_case to lower camelCase. For example: The aconfig flag
  'camera_ae_mode_low_light_boost' becomes 'cameraAeModeLowLightBoost'.

  Args:
    flag_name: str. aconfig flag in snake_case

  Return:
    Java symbol for the a config flag.
  """
  camel_case = "".join([t.capitalize() for t in flag_name.split("_")])
  # first character should be lowercase
  return camel_case[0].lower() + camel_case[1:]
