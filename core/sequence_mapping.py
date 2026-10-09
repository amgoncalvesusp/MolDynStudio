"""Explicit sequence-to-coordinate mapping; residue numbers never imply gaps."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from pathlib import Path

THREE_TO_ONE = dict(zip(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split(),
    "ARNDCQEGHILKMFPSTWYV",
))
ONE_TO_THREE = {value: key for key, value in THREE_TO_ONE.items()}


@dataclass(frozen=True)
class FastaRecord:
    record_id: str
    description: str
    sequence: str


@dataclass(frozen=True)
class SequenceGap:
    gap_id: str
    model_id: int
    chain_id: str
    source: str
    reference_id: str
    start_index: int
    end_index: int
    sequence: str
    terminal: bool
    insert_before: int
    left_residue_id: str | None
    right_residue_id: str | None

    @property
    def label(self) -> str:
        kind = "terminal" if self.terminal else "internal"
        return f"{self.source} {self.chain_id or '(blank)'}: {self.start_index}–{self.end_index} {self.sequence} ({kind})"


@dataclass(frozen=True)
class SequenceMapping:
    model_id: int
    chain_id: str
    source: str
    reference_id: str
    reference_sequence: str
    observed_sequence: str
    observed_residue_ids: tuple[str, ...]
    matched_positions: tuple[tuple[int, str], ...]
    gaps: tuple[SequenceGap, ...]
    mismatches: tuple[str, ...]
    ambiguous: bool
    blocked_reasons: tuple[str, ...]
    alignment_parameters: tuple[tuple[str, float | str], ...]


def parse_fasta(path: str | Path) -> tuple[FastaRecord, ...]:
    return parse_fasta_text(Path(path).read_bytes().decode("utf-8-sig"))


def parse_fasta_text(text: str) -> tuple[FastaRecord, ...]:
    """Parse an immutable text snapshot, allowing its caller to hash the same bytes."""
    records: list[FastaRecord] = []
    description = ""
    sequence: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if description:
                records.append(_fasta_record(description, sequence))
            description, sequence = line[1:].strip(), []
            if not description:
                raise ValueError("FASTA record requires an identifier.")
        elif not description:
            raise ValueError("FASTA sequence appears before its header.")
        else:
            sequence.append("".join(line.split()).upper())
    if description:
        records.append(_fasta_record(description, sequence))
    if not records:
        raise ValueError("FASTA contains no sequence records.")
    if len({record.record_id for record in records}) != len(records):
        raise ValueError("FASTA record identifiers must be unique.")
    return tuple(records)


def _fasta_record(description: str, pieces: list[str]) -> FastaRecord:
    sequence = "".join(pieces)
    if not sequence or any(letter not in "ACDEFGHIKLMNPQRSTVWYBXZJUO" for letter in sequence):
        raise ValueError("FASTA must contain a nonempty amino-acid sequence without gaps or stop symbols.")
    return FastaRecord(description.split()[0], description, sequence)


def align_sequence(reference: str, observed: str, residue_ids: tuple[str, ...], *,
                   chain_id: str, model_id: int = 1, source: str = "FASTA",
                   reference_id: str = "reference") -> SequenceMapping:
    """Align full reference against observed polymer with fixed, recorded scores."""
    from Bio.Align import PairwiseAligner

    if not reference or not observed or len(observed) != len(residue_ids):
        raise ValueError("Sequence mapping needs nonempty sequences and one identity per observed residue.")
    parameters = (("mode", "global"), ("match", 2.), ("mismatch", -3.),
                  ("gap_open", -6.), ("gap_extend", -.5),
                  ("query_terminal_open", -.1), ("query_terminal_extend", -.05))
    aligner = PairwiseAligner(mode="global", match_score=2., mismatch_score=-3.,
                             open_gap_score=-6., extend_gap_score=-.5)
    # A coordinate fragment may omit reference termini. Internal gaps remain costly.
    aligner.open_left_deletion_score = aligner.open_right_deletion_score = -.1
    aligner.extend_left_deletion_score = aligner.extend_right_deletion_score = -.05
    alignments = tuple(islice(iter(aligner.align(reference, observed)), 2))
    ambiguous = len(alignments) > 1
    coordinates = alignments[0].coordinates
    matched: list[tuple[int, str]] = []
    gaps: list[SequenceGap] = []
    mismatches: list[str] = []
    for index in range(coordinates.shape[1] - 1):
        r0, r1 = int(coordinates[0, index]), int(coordinates[0, index + 1])
        o0, o1 = int(coordinates[1, index]), int(coordinates[1, index + 1])
        if r1 > r0 and o1 > o0:
            for ri, oi in zip(range(r0, r1), range(o0, o1)):
                matched.append((ri + 1, residue_ids[oi]))
                if reference[ri] != observed[oi]:
                    mismatches.append(f"Reference {ri + 1} {reference[ri]} differs from {residue_ids[oi]} {observed[oi]}.")
        elif r1 > r0:
            gap_id = f"{source}:{model_id}:{chain_id}:{reference_id}:{r0 + 1}-{r1}"
            gaps.append(SequenceGap(gap_id, model_id, chain_id, source, reference_id,
                                    r0 + 1, r1, reference[r0:r1], o0 in (0, len(observed)), o0,
                                    residue_ids[o0 - 1] if o0 else None,
                                    residue_ids[o0] if o0 < len(observed) else None))
        elif o1 > o0:
            mismatches.append(f"Observed insertion without reference counterpart: {', '.join(residue_ids[o0:o1])}.")
    blocks = []
    if ambiguous:
        blocks.append("Multiple equally scoring alignments; gap reconstruction requires an unambiguous mapping.")
    if mismatches:
        blocks.append("Sequence mismatches or observed insertions prevent automatic gap reconstruction; no mutation is permitted.")
    if any(letter not in ONE_TO_THREE for letter in reference):
        blocks.append("Reference contains ambiguous or unsupported residue symbols.")
    return SequenceMapping(model_id, chain_id, source, reference_id, reference, observed,
                           residue_ids, tuple(matched), tuple(gaps), tuple(mismatches),
                           ambiguous, tuple(blocks), parameters)
