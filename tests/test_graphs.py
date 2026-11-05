from agentrec.faults import FaultSpec
from agentrec.graphs import TEMPLATES, check_graph, evaluate_graphs, make_graph_task
from agentrec.llm import MockLLM
from agentrec.provenance import Provenance
from agentrec.runner import run_workflow


def _keys(prov: Provenance) -> list[str]:
    return [s.key for s in sorted(prov.of_kind("step"), key=lambda n: n.seq)]


def _cell(rows, template, fault, method):
    return next(r for r in rows if r["template"] == template and r["fault"] == fault and r["method"] == method)


def test_each_template_records_its_call_order():
    for template, steps in TEMPLATES.items():
        world, prov, inj, _ = run_workflow(make_graph_task(template), ([], []), MockLLM())
        assert inj.fired == 0
        assert _keys(prov) == [key for key, _ in steps]
        assert check_graph(world, template)["success"]


def test_backedge_is_a_second_visit():
    _, prov, _, _ = run_workflow(make_graph_task("backedge"), ([], []), MockLLM())
    assert _keys(prov).count("a") == 1
    assert "a:2" in _keys(prov)
    later = next(s for s in prov.of_kind("step") if s.key == "a:2")
    reached = {prov.nodes[nid].key for nid in prov.descendants(later.id) if prov.nodes[nid].kind == "step"}
    assert reached == {"a:2"}


def test_graph_cases_split_where_the_edges_and_the_log_disagree():
    rows = evaluate_graphs()
    assert len(rows) == sum(len(steps) for steps in TEMPLATES.values()) * 4

    # Chain is a line, so the log order and the edges name the same steps.
    for fault in ("a", "b", "c", "d"):
        assert _cell(rows, "chain", fault, "suffix")["tainted"] == _cell(rows, "chain", fault, "coarse")["tainted"]

    # Diamond: c is later than b and never reads b.
    diamond = _cell(rows, "diamond", "b", "suffix")["tainted"]
    assert "c" in diamond
    assert "c" not in _cell(rows, "diamond", "b", "coarse")["tainted"]
    assert _cell(rows, "diamond", "b", "coarse")["tainted"] == ["b", "d"]

    # Join: q and r are later than p and never read p.
    assert _cell(rows, "join", "p", "coarse")["tainted"] == ["p", "join"]
    assert _cell(rows, "join", "p", "suffix")["tainted"] == ["p", "q", "r", "join"]
    assert _cell(rows, "join", "q", "suffix")["tainted"] == ["q", "r", "join"]
    assert _cell(rows, "join", "q", "coarse")["tainted"] == ["q", "join"]

    # Back-edge: side is later than the return and reads only the first visit.
    assert "side" in _cell(rows, "backedge", "b", "suffix")["tainted"]
    assert "side" not in _cell(rows, "backedge", "b", "coarse")["tainted"]
    assert _cell(rows, "backedge", "b", "coarse")["tainted"] == ["b", "c", "a:2"]
    assert "side" in _cell(rows, "backedge", "a:2", "suffix")["tainted"]
    assert _cell(rows, "backedge", "a:2", "coarse")["tainted"] == ["a:2"]

    for row in rows:
        if row["method"] in ("restart", "suffix", "coarse"):
            assert row["success"]
    # Support follows one evidence hop, so a longer path keeps a stale file.
    stale = {(r["template"], r["fault"]) for r in rows if r["method"] == "support" and not r["success"]}
    assert stale == {("chain", "a"), ("chain", "b"), ("diamond", "a"), ("backedge", "a"), ("backedge", "b")}


def test_corrupt_fault_is_outside_the_travel_sweep():
    from agentrec.faults import DEFAULT_FAULTS

    assert all(spec.type != "corrupt" for spec in DEFAULT_FAULTS)
    task = make_graph_task("chain")
    world, prov, inj, _ = run_workflow(task, ([], []), MockLLM(), FaultSpec("corrupt", "b", persistent=False))
    assert inj.fired == 1
    assert not check_graph(world, "chain")["success"]
    assert [prov.nodes[nid].key for nid in prov.faulty_nodes()] == ["b"]
