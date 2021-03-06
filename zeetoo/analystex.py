import argparse
import re
import csv
import codecs
import logging
from collections import defaultdict


logger = logging.getLogger(__name__)

prsr = argparse.ArgumentParser()
prsr.add_argument("source", type=str)
prsr.add_argument("dest", type=str, default="./analyses.tex")
prsr.add_argument("-s", "--sep", type=str, default="; ")
prsr.add_argument("-i", "--indent", type=str, default="\t")
prsr.add_argument(
    "-n", "--namesfile", type=str,
    help="File containing pairs of cmpd's symbol and in-latex label in csv format."
    )
verbosity = prsr.add_mutually_exclusive_group()
verbosity.add_argument(
    '--verbose', action='store_true',
    help='Print more informations to stdout.'
)
verbosity.add_argument(
    '--debug', action='store_true',
    help='Print debug logs to stdout.'
)
verbosity.add_argument(
    '--silent', action='store_true',
    help='Only errors are displayed.'
)


number = r"-?\d+(?:\.\d+)?"
decimal = r"-?\d+\.\d+"
decimalrange = decimal + r"(?: ?[-–,] ?" + decimal + ")?"
numsrange = number + r"(?: ?[-–,] ?" + number + ")?"
coupling = r"\((\w+)(?:, ?J ?= ?(.*?))?(?:, ?(\d+)\w+)\)"
hnmrshifts = "(" + decimalrange + ") ?" + coupling
rotpatt = "(?P<value>" + number + r") \(c ?= ?(?P<conc>" + decimal + r"), solv. (?P<solvent>[\w\d]+)\)"


def _parse_nmr(text, values_regex):
    match = re.match(r"(\d+\w+ NMR) \((\d+) ?(?:MHz)?, ?([\d\w]+)\)", text)
    analysis, frequency, solvent = match.groups()
    values = re.findall(values_regex, text)
    data = {
        "frequency": frequency,
        "solvent": solvent,
        "values": values,
    }
    return data


def parse_coupled_nmr(text):
    return _parse_nmr(text, hnmrshifts)


def parse_uncoupled_nmr(text):
    return _parse_nmr(text, decimal)


def parse_ir(text):
    data = {
        "method": re.search(r"\((.*?)\)", text).group(1),
        "values": re.findall(r"(\d{3,4})(?: cm-1)?", text),
    }
    return data


def parse_ms(text):
    data = {
        "method": re.search(r"\((.*?)\)", text).group(1),
        "found": re.search(r"found.*?(" + number + ")", text).group(1),
        "calcd": re.search(r"cal(?:culate)?d.*?(" + number + ")", text).group(1),
        "formula": re.search(r"for ([\w\d]*)", text).group(1),
    }
    return data


def parse_rotation(text):
    data = re.search(rotpatt, text).groupdict()
    return data


def parse_melting(text):
    return {"value": re.search(r"(" + numsrange + ")", text).group(0)}
    

def parse_yield(text):
    match = re.search(r"(\d+)%?, ([\w\s]+)", text)
    return match.groups()


def read_molecule(handle):
    """Reads one molecule data from file or file-like object. Expects one analysis
    per line in this format and order:

    <molecule identifier>
    <molecule IUPAC name>
    1H NMR (<frequency>[ MHz], <solvent>)[ = ][comma sep. list of
        <value> (<peaktype>, [J=list of <coup.const.>, ]<num.protons>H)]
    13C NMR (<frequency>[ MHz], <solvent>)[ = ][comma sep. list of <value>]
    IR (<method>)[comma sep. list of <value>[cm-1]]
    """
    data = {}
    line = None
    while not line:
        rawline = handle.readline()
        if not rawline:
            raise EOFError
        line = rawline.strip()
    logger.info(f"Parsing compound '{line}'.")
    data["id"] = line
    data["name"] = handle.readline().strip()
    data["yield"], data["form"] = parse_yield(handle.readline().strip())
    data["hnmr"] = parse_coupled_nmr(handle.readline().strip())
    data["cnmr"] = parse_uncoupled_nmr(handle.readline().strip())
    data["ir"] = parse_ir(handle.readline().strip())
    data["ms"] = parse_ms(handle.readline().strip())
    data["rotation"] = parse_rotation(handle.readline().strip())
    nextline = handle.readline().strip()
    if nextline:
        data["melting"] = parse_melting(nextline)
    return data


def format_iupac(name):
    name = re.sub(  # substitute Cahn-Ingol-Prelog descriptors
        r"\((\d*[\'\"]?\w?(?:R|S)[\d\w, \'\"]*?)\)",
        r"\\cip{\1}",
        name
    )  # only those wrapped in parentheses are substituted
    # TODO: make it handle non-parenthesized descriptors
    name = re.sub(  # substitute easily replaced specifiers
        "\\b(\\d*)(H|O|N|P|D|L|Z|E|tert|sec|cis|trans|fac|mer|sin|ter|syn|anti)\\b",
        r"\1\\\2",
        name
    )
    name = re.sub(r"\\b(\\d*)S\\b", r"\1\\Sf", name)  # for sulphur
    name = re.sub(r"(\\)?\'", r"\\chemprime", name)  # for apostrophes
    name = re.sub(r"(\\)?\"", r"\\chemprime\\chemprime", name)  # for apostrophes
    # TODO: add greek letters
    return f"\\iupac{{{name}}}"


def format_values(values):
    vals = re.findall(number, values)  # shift values
    if len(vals) > 1 and "," in values:
        vals = f"\\numlist{{{';'.join(vals)}}}"
        # using spectroscopy: vals = ", ".join(f"\\val{{{v}}}" for v in vals)
    elif len(vals) == 2:
        vals = f"\\numrange{{{vals[0]}}}{{{vals[1]}}}"
        # using spectroscopy: vals = f"\\val{{{'--'.join(vals)}}}"
    else:
        vals = f"\\num{{{vals[0]}}}"
        # using spectroscopy: vals = f"\\val{{{vals[0]}}}"
    return vals


def format_hnmr(data):
    vals = format_values(data[0])
    vals += f" ({data[1]}, "  # peak type
    if data[2]:
        jconsts = re.findall(decimal, data[2])  # coupling constants
        vals += f"\\J{{{';'.join(jconsts)}}}, "
    vals += f"\\#{{{data[3]}}})"  # num of nuclei
    return vals


def format_latex(data, sep=";", indent="\t"):
    joint = f"{sep}\n{indent}"
    latex_list = [
        f"{format_iupac(data['name'])} ({data['label']})  % {data['id']}",
        f"\\data*{{yield}} \\SI{{{data['yield']}}}{{\\percent}} ({data['form']})",
    ]
    if "melting" in data:
        latex_list.append(
            f"\\data{{mp.}} {format_values(data['melting']['value'])}\\si{{\\celsius}}"
        )
    latex_list.extend([
        f"\\data{{specific rot.}} \\num{{{data['rotation']['value']}}} "
        f"($c = {data['rotation']['conc']}$, "
        f"\\ch{{{data['rotation']['solvent']}}})",
    
        f"\\NMR({data['hnmr']['frequency']})[{data['hnmr']['solvent']}] "
        + ", ".join([format_hnmr(v) for v in data['hnmr']['values']]),

        f"\\NMR{{13,C}}({data['cnmr']['frequency']})[{data['cnmr']['solvent']}] "
        + f"\\numlist{{{'; '.join([f'{float(n):.1f}' for n in data['cnmr']['values']])}}}",
        
        f"\\data{{IR}}[{data['ir']['method']}] "
        + f"\\numlist{{{'; '.join(data['ir']['values'])}}}",

        f"\\data{{HRMS}} ({data['ms']['method']}) m/z calcd for \\ch{{{data['ms']['formula']}}}: "
        f"\\num{{{data['ms']['calcd']}}} found: \\num{{{data['ms']['found']}}}",
    ])
    latex = joint.join(latex_list)
    return f"\\begin{{experimental}}\n{indent}{latex}\n\\end{{experimental}}\n\n"
    

def get_labels(
    labelsfile=None, 
    lblfmt="\\refcmpd{{{}}}", 
    prefmt="\\textbf{{{}}}", 
    postfmt="\\textbf{{{}}}",
    separator="",
):
    labels = defaultdict(lambda: "")
    formatters = (lblfmt, prefmt, postfmt)
    if labelsfile is not None:
        with open(labelsfile, "r", newline='') as csvfile:
            csvreader = csv.reader(csvfile)
            for id_, *args in csvreader:
                formatted = [
                    fmt.format(part) for part, fmt in zip(args, formatters)
                ]
                if len(formatted) > 1:
                    formatted[0], formatted[1] = formatted[1], formatted[0]
                labels[id_] = separator.join(formatted)
    return labels


def main():
    args = prsr.parse_args()
    if args.debug:
        level = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    elif args.silent:
        level = logging.ERROR
    else:
        level = logging.WARNING
    logging.basicConfig(level=level)
    labels = get_labels(args.namesfile)
    with open(args.dest, 'w') as dest:
        with codecs.open(args.source, encoding="utf-8") as source:
            while True:
                try:
                    data = read_molecule(source)
                except EOFError:
                    logger.info("No more data found.")
                    break
                data['label'] = labels[data['id']]
                dest.write(format_latex(data, args.sep, args.indent))


if __name__ == '__main__':
    main()
