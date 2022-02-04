from setuptools import find_namespace_packages, setup
from codecs import open


with open('README.md', 'r', 'utf-8') as f:
    readme = f.read()


with open('VERSION', 'r', 'utf-8') as f:
    version = f.read()


setup(
    name='prodinf-reporting',
    version=version,
    description='',
    long_description=readme,
    packages=find_namespace_packages(include=['ensembl.*']),
    license='Apache 2.0',
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        'elasticsearch6',
        'kombu',
    ],
    python_requires='>=3.7, <4',
    classifiers=[
        'Natural Language :: English',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: Implementation :: CPython',
    ],
)
