import pytest

from runledger.gen_configs import generate, parse_space, render_configs_tsv


def test_parse_space():
    dims = parse_space("block pow2 32 512\nalpha float 0.1 2.0\nstrategy choice a b c\n")
    assert [d.kind for d in dims] == ["pow2", "float", "choice"]
    assert dims[2].choices == ["a", "b", "c"]


def test_parse_space_rejects_unknown_type():
    with pytest.raises(ValueError):
        parse_space("x wat 1 2\n")


def test_lhs_count_and_dedup():
    dims = parse_space("alpha float 0.0 1.0\nbeta float 0.0 1.0\n")
    rows = generate(dims, n=8, method="lhs", seed=0)
    assert 1 <= len(rows) <= 8
    keys = {tuple(sorted(r.items())) for r in rows}
    assert len(keys) == len(rows)  # no duplicates


def test_pow2_values_are_powers_of_two():
    dims = parse_space("block pow2 32 512\n")
    rows = generate(dims, n=20, method="grid")
    assert {int(r["block"]) for r in rows} == {32, 64, 128, 256, 512}


def test_grid_choice_enumerates_all():
    dims = parse_space("s choice x y z\n")
    rows = generate(dims, n=99, method="grid")
    assert sorted(r["s"] for r in rows) == ["x", "y", "z"]


def test_render_header_and_columns():
    dims = parse_space("alpha float 0 1\n")
    rows = generate(dims, n=2, seed=1)
    out = render_configs_tsv(rows, target="t", bin_name="b", rep=3)
    header = out.splitlines()[0].split("\t")
    assert header == ["id", "target", "bin", "ranks", "omp", "rep", "args"]
    first = out.splitlines()[1].split("\t")
    assert first[0] == "000"
    assert first[1] == "t"
    assert first[2] == "b"
    assert first[5] == "3"
    assert first[6].startswith("--alpha ")
