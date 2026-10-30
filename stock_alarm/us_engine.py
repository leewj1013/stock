"""Verified, immutable replay implementation independent of domestic entry points."""
import hashlib
import importlib
import sys
import zipfile
from pathlib import Path

BUNDLE=Path(__file__).with_name('frozen')/'us_engine_v1.zip'
BUNDLE_SHA256='51ed7ce65e056e530a694a56e4851ab17ba5db21087a5c6a40f6d65d2fcbc1d7'
SOURCE_REVISION='2439a2586d433b9580c83775b0fbc34aa7bf483f'
PACKAGE='_stock_alarm_us_v1'


def verify():
    if hashlib.sha256(BUNDLE.read_bytes()).hexdigest()!=BUNDLE_SHA256:
        raise ValueError('Frozen US replay bundle changed')


def hashes(names):
    verify()
    with zipfile.ZipFile(BUNDLE) as archive:
        return {name:hashlib.sha256(archive.read(PACKAGE+'/'+name).decode('utf-8-sig').replace('\r\n','\n').encode()).hexdigest() for name in names}


def module(name):
    verify()
    path=str(BUNDLE.resolve())
    if path not in sys.path:sys.path.insert(0,path)
    return importlib.import_module(PACKAGE+'.'+name)
