-- Runs once, when the database volume is first created. Tests use this database, never the application one.
-- The name must end in _test: the test suite refuses to run against any other database.
CREATE DATABASE rckt_test;
