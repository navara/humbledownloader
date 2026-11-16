"""Entry script for the PyInstaller build.

PyInstaller runs its entry script as a top-level module, so it cannot use the
package's relative imports; this one imports the package absolutely.
"""

from humbledownloader.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
