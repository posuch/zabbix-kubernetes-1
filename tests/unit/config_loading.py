import os

from base.config import Configuration

RESOURCES_DIR = os.path.realpath(os.path.dirname(os.path.realpath(__file__)))+"/resources"


def test_load_config():
    cfg = Configuration()
    cfg.load_config_file(f"{RESOURCES_DIR}/test.ini")
    assert (cfg.debug == True)
