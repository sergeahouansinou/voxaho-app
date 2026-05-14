"""
Configuration py2app — génère Voxaho.app
Lancer avec : python setup.py py2app
"""
from setuptools import setup

APP     = ['main.py']
NAME    = 'Voxaho'
VERSION = '1.0.1'

OPTIONS = {
    'argv_emulation': False,        # désactivé : on utilise PyQt6
    'iconfile':       'assets/AppIcon.icns',
    'plist': {
        'CFBundleName':               NAME,
        'CFBundleDisplayName':        NAME,
        'CFBundleIdentifier':         'serge.ahouansinou.voxaho',
        'CFBundleVersion':            VERSION,
        'CFBundleShortVersionString': VERSION,
        'NSMicrophoneUsageDescription':
            'Voxaho a besoin du microphone pour la dictée vocale.',
        'NSAppleEventsUsageDescription':
            'Voxaho utilise AppleScript pour injecter le texte transcrit.',
        'LSUIElement': True,         # pas d'icône dans le Dock (app de fond)
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
    },
    'packages': [
        'faster_whisper', 'sounddevice', 'numpy',
        'PyQt6', 'Quartz', 'AppKit', 'CoreFoundation',
        'pyautogui',
    ],
    # rubicon est un namespace package que py2app ne sait pas bootstrapper.
    # Il n'est pas utilisé au runtime — on l'exclut explicitement.
    'excludes': ['tkinter', 'matplotlib', 'scipy', 'PIL',
                  'rubicon', 'rubicon.objc'],
    'semi_standalone': False,
    'site_packages':   True,
}

setup(
    name    = NAME,
    version = VERSION,
    app     = APP,
    options = {'py2app': OPTIONS},
    setup_requires = ['py2app'],
)
