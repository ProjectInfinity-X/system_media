## -*- coding: utf-8 -*-
##
## Copyright (C) 2016 The Android Open Source Project
##
## Licensed under the Apache License, Version 2.0 (the "License");
## you may not use this file except in compliance with the License.
## You may obtain a copy of the License at
##
##      http://www.apache.org/licenses/LICENSE-2.0
##
## Unless required by applicable law or agreed to in writing, software
## distributed under the License is distributed on an "AS IS" BASIS,
## WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
## See the License for the specific language governing permissions and
## limitations under the License.
##
    /*@O~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~
     * The key entries below this point are generated from metadata
     * definitions in /system/media/camera/docs. Do not modify by hand or
     * modify the comment blocks at the start or end.
     *~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~*/

    private static HashSet<String> getAllCharacteristicsKeyNames() {
        HashSet<String> charsKeyNames = new HashSet<String>();
% for sec in find_all_sections(metadata):
  % for entry in find_unique_entries(sec):
    % if entry.kind == 'static' and entry.visibility in \
            ("public", "java_public", "fwk_java_public", "fwk_public"):
      % if not entry.aconfig_flag:
        charsKeyNames.add(CameraCharacteristics.${jkey_identifier(entry.name)}.getName());
      % else:
        if (Flags.${java_symbol_for_aconfig_flag(entry.aconfig_flag)}()) {
            charsKeyNames.add(CameraCharacteristics.${jkey_identifier(entry.name)}.getName());
        }
      %endif
    % endif
  % endfor
% endfor

        return charsKeyNames;
    }

    /*~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~
     * End generated code
     *~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~@~O@*/
