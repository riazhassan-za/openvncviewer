# OpenVNCViewer - a VNC viewer for macOS Screen Sharing with window scaling.
# Copyright (C) 2026 The OpenVNCViewer contributors
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""PyInstaller entry point.

PyInstaller runs its entry script as top-level ``__main__``, which leaves it
without a package context and breaks the relative imports inside
``openvncviewer/__main__.py``. Going through a real package import here keeps
the frozen build and ``python -m openvncviewer`` on the same code path.
"""

import sys

from openvncviewer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
