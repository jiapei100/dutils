DUtils -- Django Utilities
==========================

This is a collection of django related utilities.

Documentation: http://packages.python.org/dutils/

Check out `ChangeLog file
<http://github.com/amitu/dutils/blob/master/CHANGELOG.rst>`_ to see whats new.

Note To Self
============

For a new release, update dutils/__init__.py, VERSION number.::

    $ python setup.py sdist upload
    $ python setup.py build_sphinx
    $ python setup.py upload_sphinx

Sphinx required::

    $ sudo easy_install -U sphinx sphinx-pypi-upload
