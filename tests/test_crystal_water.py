"""Crystal-water preservation at the CHARMM pdb2gmx boundary."""
import json

import pytest

from core.crystal_water import prepare_crystal_waters, append_crystal_waters, consolidate_water_molecules
from tests.test_structure_audit import atom


def source(tmp_path, waters):
    path = tmp_path / 'input.pdb'
    path.write_text('\n'.join([atom(1,'CA',x=1.), *waters, 'END'])+'\n')
    return path


def oxygen(serial=2, resid=401, x=10.):
    return atom(serial,'O',resid,resname='HOH',record='HETATM',x=x)


def fake_builder(input_path, output_path):
    blocks=[]
    for index, entry in enumerate(input_path.read_text().split('$$$$')[:-1],1):
        lines=entry.strip().splitlines()
        xyz=list(map(float,lines[4].split()[:3]))
        records=[]
        for number,(name,element,dx,dy) in enumerate((('O','O.3',0.,0.),('H1','H',.96,0.),('H2','H',-.24,.93)),1):
            records.append(f'{number} {name} {xyz[0]+dx:.4f} {xyz[1]+dy:.4f} {xyz[2]:.4f} {element} 1 HOH 0')
        blocks.append(f'@<TRIPOS>MOLECULE\nWATER_{index}\n3 2 1 0 0\nSMALL\nNO_CHARGES\n@<TRIPOS>ATOM\n'+ '\n'.join(records)+'\n@<TRIPOS>BOND\n1 1 2 1\n2 1 3 1\n')
    output_path.write_text(''.join(blocks))


def test_oxygen_snapshot_and_ion_preserved_while_two_hydrogens_added(tmp_path):
    pdb=source(tmp_path,[oxygen(x=10.123), atom(3,'CA',402,resname='CA',record='HETATM',x=20.)])
    original=pdb.read_bytes()
    result=prepare_crystal_waters(pdb,tmp_path/'out',hydrogen_builder=fake_builder)
    assert result.water_count == 1
    assert pdb.read_bytes() == original
    assert 'HOH' not in result.protein_path.read_text()
    assert ' CA ' in result.protein_path.read_text()
    report=json.loads(result.provenance_path.read_text())
    assert report['observed_oxygen_count'] == 1
    assert report['modeled_hydrogen_count'] == 2
    assert report['max_observed_displacement_angstrom'] == 0.
    assert '  10.123' in result.water_pdb.read_text()
    assert len(result.water_gro.read_text().splitlines()[2:-1]) == 3
    for generated in (result.protein_path, result.water_pdb, result.water_gro):
        assert b'\r' not in generated.read_bytes()


def test_complete_water_hydrogens_are_preserved_without_builder(tmp_path):
    hydrogen2=atom(4,'H2',401,resname='HOH',record='HETATM',x=9.76)
    hydrogen2=hydrogen2[:38]+f'{2.93:8.3f}'+hydrogen2[46:]
    pdb=source(tmp_path,[oxygen(),atom(3,'H1',401,resname='HOH',record='HETATM',x=10.96),hydrogen2])
    def forbidden(*args):
        raise AssertionError('complete water must not be regenerated')
    result=prepare_crystal_waters(pdb,tmp_path/'out',hydrogen_builder=forbidden)
    report=json.loads(result.provenance_path.read_text())
    assert report['observed_hydrogen_count'] == 2
    assert report['modeled_hydrogen_count'] == 0


def test_builder_moving_observed_oxygen_is_rejected(tmp_path):
    def moved(input_path,output_path):
        fake_builder(input_path,output_path)
        output_path.write_text(output_path.read_text().replace('10.0000','11.0000'))
    with pytest.raises(ValueError,match='oxygen'):
        prepare_crystal_waters(source(tmp_path,[oxygen()]),tmp_path/'out',hydrogen_builder=moved)


def test_partial_water_hydrogens_and_virtual_site_water_are_not_guessed(tmp_path):
    pdb=source(tmp_path,[oxygen(),atom(3,'H1',401,resname='HOH',record='HETATM',x=10.96)])
    with pytest.raises(ValueError,match='zero or two'):
        prepare_crystal_waters(pdb,tmp_path/'out',hydrogen_builder=fake_builder)
    pdb=source(tmp_path,[atom(2,'O',401,resname='DOD',record='HETATM',x=10.)])
    with pytest.raises(ValueError,match='water residue'):
        prepare_crystal_waters(pdb,tmp_path/'out',hydrogen_builder=fake_builder)


def test_adds_sol_count_matching_three_water_atoms_and_preserves_protein_box(tmp_path):
    result=prepare_crystal_waters(source(tmp_path,[oxygen()]),tmp_path/'out',hydrogen_builder=fake_builder)
    gro,top=tmp_path/'protein.gro',tmp_path/'topol.top'
    row=f"{1:5d}{'ALA':<5}{'CA':>5}{1:5d}{.1:8.3f}{.2:8.3f}{.3:8.3f}"
    gro.write_text(f'Protein\n1\n{row}\n2 2 2\n')
    top.write_text('#include "charmm.ff/tip3p.itp"\n[ system ]\nProtein\n[ molecules ]\nProtein 1\n')
    append_crystal_waters(gro,top,result)
    assert b'\r' not in gro.read_bytes()
    assert b'\r' not in top.read_bytes()
    assert int(gro.read_text().splitlines()[1]) == 4
    assert gro.read_text().splitlines()[-1] == '2 2 2'
    assert 'SOL                1' in top.read_text()
    with pytest.raises(ValueError,match='already'):
        append_crystal_waters(gro,top,result)


def test_no_water_returns_without_creating_files(tmp_path):
    assert prepare_crystal_waters(source(tmp_path,[]),tmp_path/'out',hydrogen_builder=fake_builder) is None
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('second_x',(10.96,9.04))
def test_coincident_or_collinear_hydrogens_are_rejected(tmp_path,second_x):
    pdb=source(tmp_path,[oxygen(),atom(3,'H1',401,resname='HOH',record='HETATM',x=10.96),atom(4,'H2',401,resname='HOH',record='HETATM',x=second_x)])
    with pytest.raises(ValueError,match='geometry'):
        prepare_crystal_waters(pdb,tmp_path/'out',hydrogen_builder=fake_builder)


def test_generated_coincident_hydrogens_are_rejected(tmp_path):
    def degenerate(input_path,output_path):
        fake_builder(input_path,output_path)
        output_path.write_text(output_path.read_text().replace('9.7600 2.9300','10.9600 2.0000'))
    with pytest.raises(ValueError,match='geometry'):
        prepare_crystal_waters(source(tmp_path,[oxygen()]),tmp_path/'out',hydrogen_builder=degenerate)


def test_consolidates_adjacent_crystal_and_added_water_rows_without_reordering(tmp_path):
    top=tmp_path/'topol.top'
    top.write_text('[ system ]\nComplex\n[ molecules ]\nProtein_chain_A 1\nsystem 2\nSOL 269 ; observed crystal waters\n; added solvent follows\n\nSOL 13992\nSOD 2\n')
    assert consolidate_water_molecules(top) == 14261
    assert b'\r' not in top.read_bytes()
    lines=[line.split(';',1)[0].split() for line in top.read_text().splitlines()]
    assert [line for line in lines if line and line[0] in {'Protein_chain_A','system','SOL','SOD'}] == [
        ['Protein_chain_A','1'],['system','2'],['SOL','14261'],['SOD','2']]
    assert 'observed crystal waters' in top.read_text()
    assert 'added solvent follows' in top.read_text()
    first=top.read_bytes()
    assert consolidate_water_molecules(top) == 14261
    assert top.read_bytes() == first


def test_single_water_row_is_normalized_to_lf_for_genion(tmp_path):
    top = tmp_path / 'topol.top'
    original = b'[ system ]\r\nComplex\r\n[ molecules ]\r\nProtein 1\r\nSOL 14264\r\n'
    top.write_bytes(original)
    assert consolidate_water_molecules(top) == 14264
    assert top.read_bytes() == original.replace(b'\r\n', b'\n')


@pytest.mark.parametrize('rows,match',[
    ('SOL 269\nsystem 2\nSOL 13992\n','contiguous'),
    ('SOL 269\n#ifdef EXTRA_WATER\nSOL 13992\n#endif\n','conditional'),
    ('SOL 0\nSOL 10\n','positive'),
    ('SOL -1\nSOL 10\n','positive'),
    ('SOL 1.5\nSOL 10\n','integer'),
    ('SOL 2147483647\nSOL 1\n','range'),
])
def test_unsafe_solvent_topology_is_rejected_without_changing_file(tmp_path,rows,match):
    top=tmp_path/'topol.top'
    top.write_text('[ system ]\nComplex\n[ molecules ]\nProtein 1\n'+rows)
    original=top.read_bytes()
    with pytest.raises(ValueError,match=match):
        consolidate_water_molecules(top)
    assert top.read_bytes() == original
