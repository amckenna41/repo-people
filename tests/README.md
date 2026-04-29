# repo-people Tests <a name="TOP"></a>

All of the modules and functionalities of repo-people are thoroughly tested using the Python [unittest][unittest] framework. 

Module Tests
------------
* `test_export` - tests for export module and class.
* `test_repo_people` - tests for RepoPeople module and class.
* `test_users` - tests for users module and class.

Running Tests
-------------
To run all unittests, make sure you are in the main pySAR directory and from a terminal/cmd-line run:
```python
python -m unittest discover tests -v

#-v produces a more verbose and useful output
```

To run a module's specific unittests, make sure you are in the `repo-people` directory and from a terminal/cmd-line run:
```python
python -m unittest tests.test_MODULE -b
#-b output during a passing test is discarded. Output is echoed normally on test fail or error and is added to the failure messages.
```

[unittest]: https://docs.python.org/3/library/unittest.html