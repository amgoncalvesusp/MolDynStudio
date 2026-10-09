from core.sequence_mapping import align_sequence, parse_fasta


def test_numbering_jump_does_not_invent_sequence_gap():
    result = align_sequence("ACD", "ACD", ("A:238", "A:240", "A:241"), chain_id="A")
    assert result.gaps == ()
    assert not result.ambiguous


def test_repeated_sequence_has_ambiguous_alignment():
    result = align_sequence("AAAA", "AAA", ("1", "2", "3"), chain_id="A")
    assert result.ambiguous
    assert result.blocked_reasons


def test_fasta_records_preserve_identifiers(tmp_path):
    source = tmp_path / "protein.fasta"
    source.write_text(">first chain A\nACD\n>second\nEFG\n")
    assert [record.record_id for record in parse_fasta(source)] == ["first", "second"]


def test_internal_gap_and_terminals_are_distinct_and_never_mutate_residues():
    mapping = align_sequence("ACDEFGHIK", "ACDFGHIK", tuple(str(i) for i in range(8)), chain_id="A")
    assert len(mapping.gaps) == 1
    assert mapping.gaps[0].sequence == "E"
    assert not mapping.gaps[0].terminal
    assert mapping.gaps[0].insert_before == 3
    termini = align_sequence("MMACDEFGHIKSS", "ACDEFGHIK", tuple(str(i) for i in range(9)), chain_id="A")
    assert len(termini.gaps) == 2
    assert all(gap.terminal for gap in termini.gaps)


def test_mismatch_blocks_gap_modeling_without_substituting_sequence():
    mapping = align_sequence("ACDEFGHIK", "ACDYFGHIK", tuple(str(i) for i in range(9)), chain_id="A")
    assert mapping.mismatches
    assert mapping.blocked_reasons
    assert mapping.observed_sequence == "ACDYFGHIK"
