"""
License Management Plugin for LiuHao AI OS

Provides license text generation, SPDX identifiers, compatibility checks,
license header insertion, and license detection.

Capabilities:
- generate: Generate license text
- get_spdx: Get SPDX identifier
- check_compatibility: Check license compatibility
- add_header: Add license header to file content
- detect: Detect license from text
- list_licenses: List available licenses
- get_info: Get license information
- get_categories: Get license categories
"""

import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class LicenseInfo:
    """License information."""
    name: str
    spdx: str
    category: str
    type: str  # permissive, copyleft, weak_copyleft, proprietary
    description: str
    permissions: List[str]
    conditions: List[str]
    limitations: List[str]


class LicensePlugin(BasePlugin):
    """License management plugin.

    Provides license text generation, SPDX identifiers,
    compatibility checks, and header insertion.

    Example:
        plugin = LicensePlugin()
        plugin.initialize()
        plugin.activate()

        mit_license = plugin.generate("MIT", author="John Doe", year=2024)
        print(mit_license)
    """

    # License templates
    LICENSES: Dict[str, Dict[str, Any]] = {
        "MIT": {
            "spdx": "MIT",
            "category": "permissive",
            "type": "permissive",
            "description": "A short and simple permissive license with conditions only requiring preservation of copyright and license notices.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Private use",
            ],
            "conditions": [
                "License and copyright notice",
            ],
            "limitations": [
                "Liability",
                "Warranty",
            ],
            "header_template": """Copyright (c) [year] [copyright_holders]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""",
            "short_header": "MIT License",
        },
        "Apache-2.0": {
            "spdx": "Apache-2.0",
            "category": "permissive",
            "type": "permissive",
            "description": "A permissive license whose main conditions require preservation of copyright and license notices.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Patent use",
                "Private use",
            ],
            "conditions": [
                "License and copyright notice",
                "State changes",
            ],
            "limitations": [
                "Liability",
                "Trademark use",
                "Warranty",
            ],
            "header_template": """                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
""",
            "short_header": "Apache License 2.0",
        },
        "GPL-3.0": {
            "spdx": "GPL-3.0-or-later",
            "category": "copyleft",
            "type": "copyleft",
            "description": "A strong copyleft license that requires derivative works to be open sourced under the same license.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Patent use",
                "Private use",
            ],
            "conditions": [
                "Disclose source",
                "License and copyright notice",
                "Same license",
                "State changes",
            ],
            "limitations": [
                "Liability",
                "Warranty",
            ],
            "header_template": """                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) [year] [copyright_holders]
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

                            Preamble

  The GNU General Public License is a free, copyleft license for
software and other kinds of works.

[Full GPL-3.0 text omitted for brevity - use full text in production]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
""",
            "short_header": "GPL-3.0-or-later",
        },
        "GPL-2.0": {
            "spdx": "GPL-2.0-or-later",
            "category": "copyleft",
            "type": "copyleft",
            "description": "A strong copyleft license that requires derivative works to be open sourced under the same license.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Private use",
            ],
            "conditions": [
                "Disclose source",
                "License and copyright notice",
                "Same license",
                "State changes",
            ],
            "limitations": [
                "Liability",
                "Warranty",
            ],
            "header_template": """                    GNU GENERAL PUBLIC LICENSE
                       Version 2, June 1991

 Copyright (C) [year] [copyright_holders]
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

[Full GPL-2.0 text - abbreviated]

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License along
with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
""",
            "short_header": "GPL-2.0-or-later",
        },
        "LGPL-3.0": {
            "spdx": "LGPL-3.0-or-later",
            "category": "copyleft",
            "type": "weak_copyleft",
            "description": "A weak copyleft license that allows linking from non-copyleft code.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Patent use",
                "Private use",
            ],
            "conditions": [
                "Disclose source (library only)",
                "License and copyright notice",
                "Same license (library only)",
                "State changes",
            ],
            "limitations": [
                "Liability",
                "Warranty",
            ],
            "header_template": """                   GNU LESSER GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) [year] [copyright_holders]
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

[Full LGPL-3.0 text - abbreviated]

This library is free software; you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This library is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License
along with this library.  If not, see <https://www.gnu.org/licenses/>.
""",
            "short_header": "LGPL-3.0-or-later",
        },
        "BSD-3-Clause": {
            "spdx": "BSD-3-Clause",
            "category": "permissive",
            "type": "permissive",
            "description": "A permissive license similar to the BSD 2-Clause License, but with a 3rd clause that prohibits others from using the name of the project or its contributors to promote derived products.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Private use",
            ],
            "conditions": [
                "License and copyright notice",
            ],
            "limitations": [
                "Liability",
                "Warranty",
                "Trademark use",
            ],
            "header_template": """BSD 3-Clause License

Copyright (c) [year], [copyright_holders]
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
""",
            "short_header": "BSD-3-Clause",
        },
        "BSD-2-Clause": {
            "spdx": "BSD-2-Clause",
            "category": "permissive",
            "type": "permissive",
            "description": "A permissive license that comes in two forms: the New/Revised BSD License and the Simplified/FreeBSD License.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Private use",
            ],
            "conditions": [
                "License and copyright notice",
            ],
            "limitations": [
                "Liability",
                "Warranty",
            ],
            "header_template": """BSD 2-Clause License

Copyright (c) [year], [copyright_holders]
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
""",
            "short_header": "BSD-2-Clause",
        },
        "ISC": {
            "spdx": "ISC",
            "category": "permissive",
            "type": "permissive",
            "description": "A permissive license functionally equivalent to the Simplified BSD License.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Private use",
            ],
            "conditions": [
                "License and copyright notice",
            ],
            "limitations": [
                "Liability",
                "Warranty",
            ],
            "header_template": """ISC License

Copyright (c) [year] [copyright_holders]

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
""",
            "short_header": "ISC",
        },
        "MPL-2.0": {
            "spdx": "MPL-2.0",
            "category": "copyleft",
            "type": "weak_copyleft",
            "description": "A weak copyleft file-level license. If you modify MPL-licensed files and distribute them, you must make those modifications available under MPL.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Patent use",
                "Private use",
            ],
            "conditions": [
                "Disclose source (file-level)",
                "License and copyright notice",
            ],
            "limitations": [
                "Liability",
                "Trademark use",
                "Warranty",
            ],
            "header_template": """Mozilla Public License Version 2.0
==================================

1. Definitions
--------------

[Full MPL-2.0 text - abbreviated]

This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.

Copyright (c) [year] [copyright_holders]
""",
            "short_header": "MPL-2.0",
        },
        "Unlicense": {
            "spdx": "Unlicense",
            "category": "public_domain",
            "type": "public_domain",
            "description": "A license that dedicates works to the public domain.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Private use",
            ],
            "conditions": [],
            "limitations": [
                "Liability",
                "Warranty",
            ],
            "header_template": """This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <https://unlicense.org>
""",
            "short_header": "Unlicense",
        },
        "CC0-1.0": {
            "spdx": "CC0-1.0",
            "category": "public_domain",
            "type": "public_domain",
            "description": "Creative Commons Zero v1.0 Universal - dedicates works to the public domain.",
            "permissions": [
                "Commercial use",
                "Distribution",
                "Modification",
                "Private use",
            ],
            "conditions": [],
            "limitations": [
                "Liability",
                "Patent use",
                "Trademark use",
                "Warranty",
            ],
            "header_template": """Creative Commons Legal Code

CC0 1.0 Universal

    CREATIVE COMMONS CORPORATION IS NOT A LAW FIRM AND DOES NOT PROVIDE
    LEGAL SERVICES. DISTRIBUTION OF THIS DOCUMENT DOES NOT CREATE AN
    ATTORNEY-CLIENT RELATIONSHIP. CREATIVE COMMONS PROVIDES THIS
    INFORMATION ON AN "AS-IS" BASIS. CREATIVE COMMONS MAKES NO WARRANTIES
    REGARDING THE USE OF THIS DOCUMENT OR THE INFORMATION OR WORKS
    PROVIDED HEREUNDER, AND DISCLAIMS LIABILITY FOR DAMAGES RESULTING FROM
    THE USE OF THIS DOCUMENT OR THE INFORMATION OR WORKS PROVIDED
    HEREUNDER.

[Full CC0 text - abbreviated]

The person who associated a work with this deed has dedicated the work to
the public domain by waiving all of his or her rights to the work worldwide
under copyright law, including all related and neighboring rights, to the
extent allowed by law.

You can copy, modify, distribute and perform the work, even for commercial
purposes, all without asking permission.
""",
            "short_header": "CC0-1.0",
        },
    }

    # License compatibility matrix (simplified)
    # Can license A code be used in a project with license B?
    COMPATIBILITY: Dict[str, Dict[str, bool]] = {
        "MIT": {
            "MIT": True, "Apache-2.0": True, "GPL-3.0": True, "GPL-2.0": True,
            "LGPL-3.0": True, "BSD-3-Clause": True, "BSD-2-Clause": True,
            "ISC": True, "MPL-2.0": True, "Unlicense": True, "CC0-1.0": True,
        },
        "Apache-2.0": {
            "MIT": True, "Apache-2.0": True, "GPL-3.0": True, "GPL-2.0": False,
            "LGPL-3.0": True, "BSD-3-Clause": True, "BSD-2-Clause": True,
            "ISC": True, "MPL-2.0": True, "Unlicense": True, "CC0-1.0": True,
        },
        "GPL-3.0": {
            "MIT": False, "Apache-2.0": False, "GPL-3.0": True, "GPL-2.0": False,
            "LGPL-3.0": False, "BSD-3-Clause": False, "BSD-2-Clause": False,
            "ISC": False, "MPL-2.0": False, "Unlicense": False, "CC0-1.0": False,
        },
        "GPL-2.0": {
            "MIT": False, "Apache-2.0": False, "GPL-3.0": True, "GPL-2.0": True,
            "LGPL-3.0": False, "BSD-3-Clause": False, "BSD-2-Clause": False,
            "ISC": False, "MPL-2.0": False, "Unlicense": False, "CC0-1.0": False,
        },
        "BSD-3-Clause": {
            "MIT": True, "Apache-2.0": True, "GPL-3.0": True, "GPL-2.0": True,
            "LGPL-3.0": True, "BSD-3-Clause": True, "BSD-2-Clause": True,
            "ISC": True, "MPL-2.0": True, "Unlicense": True, "CC0-1.0": True,
        },
    }

    def __init__(self) -> None:
        """Initialize the license plugin."""
        metadata = PluginMetadata(
            name="license",
            version="1.0.0",
            description="License generation, detection, and compatibility checking",
            author="LiuHao AI OS Team",
            categories=["development", "legal", "utility"],
            keywords=["license", "spdx", "legal", "copyright", "open-source"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_license: str = "MIT"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the license plugin."""
        try:
            if config:
                self._default_license = config.get("default_license", "MIT")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the license plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the license plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the license plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate",
            "get_spdx",
            "check_compatibility",
            "add_header",
            "detect",
            "list_licenses",
            "get_info",
            "get_categories",
        ]

    # ==================== Generation ====================

    def generate(
        self,
        license_name: Optional[str] = None,
        *,
        author: str = "",
        year: Optional[int] = None,
        project: str = "",
    ) -> str:
        """Generate a license text.

        Args:
            license_name: Name of the license (e.g., "MIT", "GPL-3.0").
            author: Copyright holder name(s).
            year: Copyright year.
            project: Project name.

        Returns:
            Full license text.
        """
        self._ensure_active()
        self._increment_call_count()

        name = license_name or self._default_license
        license_data = self._get_license(name)

        if not license_data:
            raise ValueError(
                f"Unknown license: {name}. "
                f"Available: {list(self.LICENSES.keys())}"
            )

        template = license_data["header_template"]
        current_year = year or datetime.now().year
        holders = author or "Copyright Holders"

        # Replace placeholders
        result = template.replace("[year]", str(current_year))
        result = result.replace("[yyyy]", str(current_year))
        result = result.replace("[copyright_holders]", holders)
        result = result.replace("[name of copyright owner]", holders)

        if project:
            result = result.replace("[project]", project)

        return result

    def _get_license(self, name: str) -> Optional[Dict[str, Any]]:
        """Get license data by name (case-insensitive)."""
        for lic_name, data in self.LICENSES.items():
            if lic_name.lower() == name.lower():
                return data
            if data["spdx"].lower() == name.lower():
                return data
        return None

    # ==================== SPDX ====================

    def get_spdx(self, license_name: str) -> str:
        """Get SPDX identifier for a license.

        Args:
            license_name: Name of the license.

        Returns:
            SPDX identifier string.
        """
        self._ensure_active()
        self._increment_call_count()

        license_data = self._get_license(license_name)
        if not license_data:
            raise ValueError(f"Unknown license: {license_name}")

        return license_data["spdx"]

    # ==================== Compatibility ====================

    def check_compatibility(
        self,
        source_license: str,
        target_license: str,
    ) -> Tuple[bool, str]:
        """Check if source-licensed code can be used in a target-licensed project.

        Args:
            source_license: License of the source code.
            target_license: License of the target project.

        Returns:
            Tuple of (is_compatible, reason).
        """
        self._ensure_active()
        self._increment_call_count()

        src = self._resolve_license(source_license)
        tgt = self._resolve_license(target_license)

        if not src or not tgt:
            unknown = source_license if not src else target_license
            return False, f"Unknown license: {unknown}"

        # Check compatibility matrix
        if src in self.COMPATIBILITY and tgt in self.COMPATIBILITY[src]:
            compatible = self.COMPATIBILITY[src][tgt]
            if compatible:
                return True, f"{src} code can be used in {tgt} projects."
            else:
                return False, f"{src} code cannot be used in {tgt} projects due to copyleft incompatibility."

        # Heuristic fallback
        src_type = self.LICENSES[src]["type"]
        tgt_type = self.LICENSES[tgt]["type"]

        if src_type == "public_domain":
            return True, "Public domain works can be used in any project."
        elif src_type == "permissive":
            if tgt_type in ("permissive", "weak_copyleft", "copyleft"):
                return True, f"Permissive {src} is generally compatible with {tgt_type} {tgt}."
            return False, "Incompatible license types"
        elif src_type == "weak_copyleft":
            if tgt_type in ("weak_copyleft", "copyleft"):
                return True, f"Weak copyleft {src} is compatible with {tgt_type} {tgt}."
            elif tgt_type == "permissive":
                return False, f"Weak copyleft {src} cannot be used in permissive {tgt} projects."
            return False, "Incompatible license types"
        elif src_type == "copyleft":
            if tgt_type == "copyleft":
                return True, f"Strong copyleft licenses are mutually compatible (same license required)."
            return False, f"Strong copyleft {src} cannot be used in {tgt_type} {tgt} projects."

        return False, "Unknown compatibility"

    def _resolve_license(self, name: str) -> Optional[str]:
        """Resolve a license name to its canonical key."""
        for lic_name, data in self.LICENSES.items():
            if lic_name.lower() == name.lower():
                return lic_name
            if data["spdx"].lower() == name.lower():
                return lic_name
        return None

    # ==================== Header Insertion ====================

    def add_header(
        self,
        content: str,
        license_name: str,
        *,
        author: str = "",
        year: Optional[int] = None,
        language: str = "python",
    ) -> str:
        """Add a license header to source file content.

        Args:
            content: File content.
            license_name: License name.
            author: Copyright holder.
            year: Copyright year.
            language: Programming language (determines comment style).

        Returns:
            Content with license header prepended.
        """
        self._ensure_active()
        self._increment_call_count()

        license_data = self._get_license(license_name)
        if not license_data:
            raise ValueError(f"Unknown license: {license_name}")

        current_year = year or datetime.now().year
        holders = author or "Copyright Holders"

        comment_styles = {
            "python": ('"""', '"""', "#"),
            "javascript": ("/*", "*/", "*"),
            "java": ("/*", "*/", "*"),
            "cpp": ("/*", "*/", "*"),
            "go": ("/*", "*/", "*"),
            "rust": ("/*", "*/", "*"),
            "csharp": ("/*", "*/", "*"),
            "php": ("/*", "*/", "*"),
            "ruby": ("=begin", "=end", "#"),
            "html": ("<!--", "-->", ""),
            "css": ("/*", "*/", "*"),
            "shell": ("#", "", "#"),
            "perl": ("=pod", "=cut", "#"),
        }

        style = comment_styles.get(language.lower(), comment_styles["python"])
        start, end, prefix = style

        spdx_line = f"SPDX-License-Identifier: {license_data['spdx']}"
        copyright_line = f"Copyright (c) {current_year} {holders}"
        short_header = license_data["short_header"]

        header_lines = []
        header_lines.append(f"{start}")
        header_lines.append(f"{prefix} {short_header}")
        header_lines.append(f"{prefix}")
        header_lines.append(f"{prefix} {copyright_line}")
        header_lines.append(f"{prefix} {spdx_line}")
        if end:
            header_lines.append(f"{end}")
        header_lines.append("")

        # Remove shebang if present (add header after shebang)
        lines = content.split("\n")
        if lines and lines[0].startswith("#!"):
            result = [lines[0], ""] + header_lines + lines[1:]
        else:
            result = header_lines + lines

        return "\n".join(result)

    # ==================== Detection ====================

    def detect(self, text: str) -> List[Dict[str, Any]]:
        """Detect the license from a text.

        Args:
            text: Text to analyze (license file or source code).

        Returns:
            List of detected licenses with confidence scores.
        """
        self._ensure_active()
        self._increment_call_count()

        text_lower = text.lower()
        results = []

        for name, data in self.LICENSES.items():
            score = 0.0

            # Check for SPDX identifier
            spdx = data["spdx"].lower()
            if spdx in text_lower:
                score += 0.5

            # Check for license name
            if name.lower() in text_lower:
                score += 0.2

            # Check for keywords
            keywords_map = {
                "MIT": ["permission is hereby granted", "free of charge", "software is provided \"as is\""],
                "Apache-2.0": ["apache license", "version 2.0", "licensed under the apache license"],
                "GPL-3.0": ["gnu general public license", "version 3", "copyleft"],
                "GPL-2.0": ["gnu general public license", "version 2", "gpl v2"],
                "LGPL-3.0": ["lesser general public license", "lgpl"],
                "BSD-3-Clause": ["bsd 3-clause", "neither the name of the copyright holder"],
                "BSD-2-Clause": ["bsd 2-clause", "redistributions of source code"],
                "ISC": ["isc license", "permission to use, copy, modify"],
                "MPL-2.0": ["mozilla public license", "mpl 2.0"],
                "Unlicense": ["public domain", "unlicense", "free and unencumbered"],
                "CC0-1.0": ["creative commons zero", "cc0", "public domain"],
            }

            keywords = keywords_map.get(name, [])
            for kw in keywords:
                if kw.lower() in text_lower:
                    score += 0.1

            # Check for type indicators
            if data["type"] == "copyleft" and "copyleft" in text_lower:
                score += 0.1

            # Cap at 1.0
            score = min(score, 1.0)

            if score > 0:
                results.append({
                    "license": name,
                    "spdx": data["spdx"],
                    "confidence": round(score, 2),
                    "category": data["category"],
                })

        # Sort by confidence
        results.sort(key=lambda x: x["confidence"], reverse=True)

        return results

    # ==================== Query ====================

    def list_licenses(self, *, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all available licenses.

        Args:
            category: Optional category filter.

        Returns:
            List of license info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        licenses = []
        for name, data in self.LICENSES.items():
            if category and data["category"] != category:
                continue
            licenses.append({
                "name": name,
                "spdx": data["spdx"],
                "category": data["category"],
                "type": data["type"],
                "description": data["description"],
            })

        return licenses

    def get_info(self, license_name: str) -> LicenseInfo:
        """Get detailed information about a license.

        Args:
            license_name: Name of the license.

        Returns:
            LicenseInfo with full details.
        """
        self._ensure_active()
        self._increment_call_count()

        license_data = self._get_license(license_name)
        if not license_data:
            raise ValueError(f"Unknown license: {license_name}")

        # Find the canonical name
        canonical_name = license_name
        for name, data in self.LICENSES.items():
            if data is license_data:
                canonical_name = name
                break

        return LicenseInfo(
            name=canonical_name,
            spdx=license_data["spdx"],
            category=license_data["category"],
            type=license_data["type"],
            description=license_data["description"],
            permissions=list(license_data["permissions"]),
            conditions=list(license_data["conditions"]),
            limitations=list(license_data["limitations"]),
        )

    def get_categories(self) -> List[str]:
        """Get license categories.

        Returns:
            List of category names.
        """
        self._ensure_active()
        self._increment_call_count()

        categories = set()
        for data in self.LICENSES.values():
            categories.add(data["category"])

        return sorted(categories)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        categories: Dict[str, int] = {}
        types: Dict[str, int] = {}

        for data in self.LICENSES.values():
            categories[data["category"]] = categories.get(data["category"], 0) + 1
            types[data["type"]] = types.get(data["type"], 0) + 1

        return {
            "total_licenses": len(self.LICENSES),
            "categories": categories,
            "types": types,
            "default_license": self._default_license,
        }
