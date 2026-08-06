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
"""Entry point: python -m openvncviewer [--host HOST] [--port PORT] [--user USER]"""

import argparse
import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .ui import WHEEL_SPEED_DEFAULT, MainWindow

DEFAULT_PORT = 5900


def main():
    parser = argparse.ArgumentParser(
        prog="openvncviewer",
        description="VNC viewer for macOS Screen Sharing, scaled to the window")
    parser.add_argument("--host", default="", help="hostname or IP of the Mac")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"VNC port (default: {DEFAULT_PORT})")
    parser.add_argument("--user", default="", help="macOS account name")
    parser.add_argument("--version", action="version",
                        version=f"openvncviewer {__version__}")
    args = parser.parse_args()

    # The password is deliberately not a command-line option: it would end up
    # in shell history and in the process list.
    app = QApplication(sys.argv)
    app.setApplicationName("OpenVNCViewer")
    app.setApplicationVersion(__version__)

    window = MainWindow()
    window.last_connection = (args.host, args.port, args.user,
                              WHEEL_SPEED_DEFAULT)
    window.show()
    window.prompt_connect()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
