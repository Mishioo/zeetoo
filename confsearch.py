import argparse
import csv
import os
import time
import logging as lgg
from math import floor, sqrt
import rdkit
from rdkit import Chem
from rdkit.Chem import AllChem


def get_args(argv=None):
    prsr = argparse.ArgumentParser(
        description="Perform a conformational search on given molecules."
    )
    group = prsr.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-m', '--molecules', nargs='+',
        help='One or more files with molecule specification.'
    )
    group.add_argument(
        '-d', '--directory',
        help='Directory with .mol files.'
    )
    prsr.add_argument(
        '-o', '--output_dir', default='.\\confsearch', help='Output directory.'
    )
    prsr.add_argument(
        '-n', '--num_confs', type=int, default=10,
        help='Number of cnformers to generate.'
    )
    prsr.add_argument(
        '-r', '--rms_tresh', type=float, default=1,
        help='Maximum RMSD of conformers.'
    )
    prsr.add_argument(
        '-e', '--energy_window', type=float, default=5,
        help='Maximum energy difference from lowest-energy conformer '
        'in kcal/mol.'
    )
    prsr.add_argument(
        '-c', '--max_cycles', type=int, default=10,
        help='Maximum number of energy minimization cycles.'
    )
    prsr.add_argument(
        '-f', '--fixed', type=int, nargs='+', default=(),
        help='Indices of atoms constrained.'
    )
    prsr.add_argument(
        '-x', '--max_displ', type=float,
        help='Maximum displacement of constrained atom. If not given, atoms '
             'constrained are fixed in place.'
    )
    prsr.add_argument(
        '-V', '--verbose', action='store_true'
    )
    prsr.add_argument(
        '-D', '--debug', action='store_true'
    )
    return prsr.parse_args(argv)
    

def find_lowest_energy_conformer(
        molecule, num_confs, rms_tresh, max_cycles, coord_map, max_displ
):
    ids = AllChem.EmbedMultipleConfs(
        molecule, numConfs=num_confs, pruneRmsThresh=rms_tresh,
        coordMap=coord_map
    )
    lgg.info("Conformers initialized, starting minimization.")
    min_en, min_id = float('inf'), -1
    energies = {}
    stereo = Chem.FindMolChiralCenters(molecule)
    mp = AllChem.MMFFGetMoleculeProperties(molecule)
    for cid in ids:
        if cid and cid % 100 == 0:
            lgg.info(f"Minimization progress: {cid}/{len(ids)}")
        ff = AllChem.MMFFGetMoleculeForceField(molecule, mp, confId=cid)
        for atom in coord_map:
            if max_displ is not None:
                ff.MMFFAddPositionConstraint(
                    atom, maxDispl=max_displ, forceConstant=1e5
                )
            else:
                ff.AddFixedPoint(atom)
        ff.Initialize()
        for cycle in range(max_cycles):
            if not ff.Minimize():
                # ff.Minimize() returns 0 on success
                break
        else:
            molecule.RemoveConformer(cid)
            lgg.debug(f"Conf {cid} ignored: ff.Minimize() unsuccessfull")  
            continue
        energy = ff.CalcEnergy()
        lgg.debug(f"Conf {cid} lowest energy: {energy}")
        energies[cid] = energy
        if energy < min_en:
            lgg.debug(f"Conf {cid} is new min with energy {energy}")
            min_en, min_id = energy, cid
        lgg.debug(
            f"Conf {cid} energy: {energy} kcal/mol"
        )
    lgg.info(
        f"Lowest conformer found: {min_en} kcal/mol"
    )
    return molecule, min_id, min_en, energies
    

def rms_sieve(molecule, energies, threshold):
    AllChem.AlignMolConformers(molecule)
    indices = {n: c.GetId() for n, c in enumerate(molecule.GetConformers())}
    noh = Chem.RemoveHs(molecule)
    rmsmatrix = AllChem.GetConformerRMSMatrix(noh)
    for i, rms in enumerate(rmsmatrix):
        first = floor((sqrt(8*i+1) - 1) / 2)
        second = indices[i - first * (first + 1) // 2]
        first = indices[first + 1]
        if rms > threshold:
            continue
        try:
            fen = energies[first]
            sen = energies[second]
        except KeyError:
            continue
        throwaway = first if fen > sen else second
        molecule.RemoveConformer(throwaway)
        del energies[throwaway]
        lgg.debug(f"Conf {throwaway} ignored: rms under threshold.")
    return molecule
    
    
def energy_sieve(molecule, energies, threshold):
    minen = min(energies.values())
    maxen = minen + threshold
    for cid, en in energies.items():
        if en > maxen:
            molecule.RemoveConformer(cid)
            lgg.debug(
                f"Conf {cid} ignored: energy {en} "
                f"higher than theshold {maxen}"
            )
    return molecule
    
    
def main(argv=None):
    args = get_args(argv)
    if args.verbose:
        level = lgg.INFO
    elif args.debug:
        level = lgg.DEBUG
    else:
        level = lgg.WARNING
    lgg.basicConfig(level=level)
    lgg.info(f"Confsearch by Michał M. Więcław")
    
    if args.molecules is None:
        mol_names = [name for name in os.listdir(args.directory)
                     if name.endswith('.mol')]
        molecules = [args.directory + '\\' + name for name in mol_names]
    else:
        molecules = args.molecules
        mol_names = [mol.split('\\')[-1] for mol in molecules]
    lgg.debug(f"molecules: {molecules}")
    lgg.debug(f"molecules names: {mol_names}")
    
    os.makedirs(args.output_dir, exist_ok=True)
    report_file = args.output_dir + '\\' + \
        f'confsearch_report.txt'
    with open(report_file, 'w') as report:
        report.write(
            f"Confsearch -- RMSD treshold   = {args.rms_tresh} Anstrom,\n"
            f"              energy window   = {args.energy_window} kcal/mol,\n"
            f"              confs requested = {args.num_confs}\n\n"
        )
        report.write(f"Energies values of most stable conformers:\n")
        longest = max(map(len, mol_names))
        for mol, name in zip(molecules, mol_names):
            lgg.info(f"Starting with molecule {name}")
            m = Chem.MolFromMolFile(mol, removeHs=False)
            if m is not None:
                lgg.info('Molecule loaded.')
            else:
                lgg.warning(f"Couldn't load molecule {name}")
                continue
                
            atoms = {}
            for atnum in (atom.GetAtomicNum() for atom in m.GetAtoms()):
                atoms[atnum] = atoms.get(atnum, 0) + 1
            lgg.debug(f"atoms in structure: {atoms}")
            Chem.AssignStereochemistryFrom3D(m)
            lgg.debug(f"Stereochemistry found: {Chem.FindMolChiralCenters(m)}")
            
            conf = m.GetConformer()
            coord_map = {n: conf.GetAtomPosition(n) for n in args.fixed}
            
            m, cid, en, ens = find_lowest_energy_conformer(
                m, args.num_confs, args.rms_tresh, args.max_cycles, coord_map,
                args.max_displ
            )
            num = m.GetNumConformers()
            lgg.info(f"Number of conformers optimized: {num}")
            m = energy_sieve(m, ens, args.energy_window)
            lgg.info(
                f"{num-m.GetNumConformers()} conformers outside energy window."
            )
            num = m.GetNumConformers()
            m = rms_sieve(m, ens, args.rms_tresh)
            lgg.info(
                f"{num-m.GetNumConformers()} conformers discarded by "
                "RMS sieve."
            )
            lgg.info(f"Number of conformers generated: {m.GetNumConformers()}")
            molrepr = Chem.MolToMolBlock(m, confId=cid)
            molfilename = mol.split('\\')[-1]
            outfile = args.output_dir + '\\' + molfilename.split('.')[-2] \
                + '_min_conf.mol'
            with open(outfile, 'w') as file:
                file.write(molrepr)
            lgg.info(f"Lowest energy conformer saved to {outfile}")
            report.write(
                f"{name: <{longest}} = {en: > 13.8f} kcal/mol\n"
            )
            sdfile = args.output_dir + '\\' + molfilename.split('.')[-2] \
                + '_confs.sdf'
            writer = Chem.SDWriter(sdfile)
            for conf in m.GetConformers():
                writer.write(m, confId=conf.GetId())
            writer.close()
    
    
if __name__ == '__main__':

    main()
