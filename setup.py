from setuptools import setup, find_packages

setup(
    name="indemnify-cli",
    version="1.0.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "indemnify=daemon.cli:main"
        ]
    },
    # Ensure the docs are included if distributed
    package_data={
        "": ["docs/AGENT_MANUAL.md"]
    }
)
