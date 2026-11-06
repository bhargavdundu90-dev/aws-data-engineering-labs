import os

import pytest

os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")


@pytest.fixture(scope="session")
def spark():
    from pyspark.sql import SparkSession

    s = (SparkSession.builder.master("local[2]").appName("lab09-tests")
         .config("spark.ui.enabled", "false")
         .config("spark.sql.shuffle.partitions", "2")
         .config("spark.sql.session.timeZone", "UTC")
         .config("spark.driver.host", "127.0.0.1")
         .getOrCreate())
    yield s
    s.stop()
