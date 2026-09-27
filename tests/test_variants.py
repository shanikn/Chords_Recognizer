"""packaging/variants.py: which packages only the notes feature needs (lite leaves them out)."""

import importlib.util
import sys
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    # packaging/ isn't a package (and its name is taken by the `packaging` library).
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


variants = _load("variants")


class _Dist:
    def __init__(self, requires):
        self.requires = requires


def _fake_tree(monkeypatch, tree):
    def distribution(name):
        try:
            return _Dist(tree[variants.normal(name)])
        except KeyError:
            raise metadata.PackageNotFoundError(name) from None

    monkeypatch.setattr(variants.metadata, "distribution", distribution)


# The walk is depth-first (a stack), so chordchart's requirements are visited last to
# first: demucs (notes) reaches lib[fast] before anything else; without notes, "base"
# reaches plain lib before "chordside" asks for lib[fast].
TREE = {
    "chordchart": ["chordside", "base", "demucs"],
    "demucs": ["lib[fast]", "torch"],
    "base": ["lib"],
    "chordside": ["lib[fast]"],
    "lib": ['speedups; extra == "fast"'],
    "speedups": [],
    "torch": [],
}


def test_extras_on_a_revisit_are_followed(monkeypatch):
    _fake_tree(monkeypatch, TREE)
    without_notes = variants.reachable(["chordchart"], {"demucs"})
    assert "speedups" in without_notes  # lib[fast], reached after plain lib


def test_a_package_the_chords_need_is_not_notes_only(monkeypatch):
    _fake_tree(monkeypatch, TREE)
    assert variants.notes_only() == {"demucs", "torch"}


def test_cycles_end(monkeypatch):
    _fake_tree(
        monkeypatch,
        {
            "chordchart": ["a[x]"],
            "a": ['b[y]; extra == "x"', "b"],
            "b": ['a[x]; extra == "y"', "a"],
        },
    )
    assert set(variants.reachable(["chordchart"], set())) == {"chordchart", "a", "b"}


@pytest.mark.parametrize("key", sorted(variants.VARIANTS))
def test_each_variant_has_its_own_log_file(key):
    from chordchart.desktop.app import log_name

    variant = variants.VARIANTS[key]
    assert log_name(notes=variant.notes) == variant.log_file
    assert len({v.log_file for v in variants.VARIANTS.values()}) == len(variants.VARIANTS)
