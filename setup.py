import os
import os.path

from setuptools import setup

BASEDIR = os.path.abspath(os.path.dirname(__file__))


def required(requirements_file):
    """ Read requirements file and remove comments and empty lines. """
    base_dir = os.path.abspath(os.path.dirname(__file__))
    with open(os.path.join(base_dir, requirements_file), 'r') as f:
        requirements = f.read().splitlines()
        if 'MYCROFT_LOOSE_REQUIREMENTS' in os.environ:
            print('USING LOOSE REQUIREMENTS!')
            requirements = [r.replace('==', '>=') for r in requirements]
        return [pkg for pkg in requirements
                if pkg.strip() and not pkg.startswith("#")]


def get_version():
    """ Find the version of ovos-core"""
    version = None
    version_file = os.path.join(BASEDIR, 'ovos_bus_client', 'version.py')
    major, minor, build, alpha = (None, None, None, None)
    with open(version_file) as f:
        for line in f:
            if 'VERSION_MAJOR' in line:
                major = line.split('=')[1].strip()
            elif 'VERSION_MINOR' in line:
                minor = line.split('=')[1].strip()
            elif 'VERSION_BUILD' in line:
                build = line.split('=')[1].strip()
            elif 'VERSION_ALPHA' in line:
                alpha = line.split('=')[1].strip()

            if ((major and minor and build and alpha) or
                    '# END_VERSION_BLOCK' in line):
                break
    version = f"{major}.{minor}.{build}"
    if int(alpha):
        version += f"a{alpha}"
    return version


setup(
    name='ovos-bus-client',
    version=get_version(),
    packages=['ovos_bus_client',
              'ovos_bus_client.client',
              'ovos_bus_client.util'],
    package_data={
        '*': ['*.txt', '*.md']
    },
    include_package_data=True,
    install_requires=required('requirements.txt'),
    url='https://github.com/OpenVoiceOS/ovos-bus-client',
    license='Apache-2.0',
    author='JarbasAI',
    author_email='jarbasai@mailfence.com',
    description='OVOS Messagebus Client',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',

        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    entry_points={
        'console_scripts': [
            'ovos-listen=ovos_bus_client.scripts:ovos_listen',
            'ovos-speak=ovos_bus_client.scripts:ovos_speak',
            'ovos-say-to=ovos_bus_client.scripts:ovos_say_to',
            'ovos-simple-cli=ovos_bus_client.scripts:simple_cli'
        ]
    }
)
