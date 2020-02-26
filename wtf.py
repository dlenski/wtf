#!/usr/bin/env python
from __future__ import print_function
import argparse
from sys import stdin, stdout, stderr, exit, version_info
import re
import os
import shutil
from tempfile import NamedTemporaryFile

# Quacks like a dict and an object
class slurpy(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(*e.args)
    def __setattr__(self, k, v):
        self[k]=v

# Add several related options at once (e.g. --something, --no-something, --ignore-something)
def multi_opt(p, *args, **kw):
    values = kw.pop('values', ('fix','report',None))
    longs = kw.pop('longs', ('','report-','ignore-'))
    shorts = kw.pop('shorts', ('', lambda s: s.upper() if s.upper()!=s else s.lower() if s.lower()!=s else s, 'I'))

    g=p.add_mutually_exclusive_group(required=False)

    d = dict(kw)
    d['action']='store_const'

    for v, l, s in zip(values, longs, shorts):
        d['const'] = v

        # mangle the flags, e.g.
        #   '-a', '--allow' becomes '-A', '--no-allow'
        a2 = []
        for a in args:
            if a.startswith('--'):
                if hasattr(l, '__call__'):
                    a2.append('--%s' % (l(a[2:])))
                else:
                    a2.append('--%s' % (l+a[2:]))
            elif a.startswith('-'):
                if hasattr(s, '__call__'):
                    a2.append('-%s' % s(a[1:]))
                else:
                    a2.append('-%s' % (s+a[1:]))
            else:
                a2.append(a)

        opt = g.add_argument(*a2, **d)
        d.setdefault('dest', opt.dest)

        # no help or dest after first option
        if 'help' in d: del d['help']
        if 'default' in d: del d['default']

    return g

class StoreTupleAction(argparse.Action):
    def __call__(self, p, ns, values, ostr):
        setattr(ns, self.dest, (self.const, values))

native_eol = os.linesep.encode()
eol_name2val = {'crlf':b'\r\n', 'lf':b'\n', 'cr':b'\r', 'native':native_eol, 'first':None}
eol_val2name = {b'\r\n':'crlf', b'\n':'lf', b'\r':'cr', None:'unknown'}
nullout = open(os.devnull, 'wb')

# need binary streams in Python3
if version_info >= (3,0):
    stdin = stdin.buffer
    stdout = stdout.buffer

# make stdin and stdout not do any EOL translations on Windows
if os.name=='nt':
    import msvcrt
    msvcrt.setmode(stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(stdout.fileno(), os.O_BINARY)

def parse_args():
    p = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
        description='''
        Whitespace Total Fixer. Fixes and/or reports all manner of
        annoying issues with whitespace or line endings in text files.

        Exit codes on successful operation:
            0: no issues seen
            10: issues fixed
            20: unfixed issues seen

        http://github.com/dlenski/wtf''')

    g=p.add_argument_group("Input/output modes")
    g.add_argument('inf', metavar="textfile", nargs='*', type=argparse.FileType('rb'), help='Input file(s)', default=[stdin])

    g2=g.add_mutually_exclusive_group(required=False)
    g2.add_argument('-o', metavar="outfile", dest='outf', type=argparse.FileType('wb'), help='Output file (default is stdout)', default=stdout)
    g2.add_argument('-0', '--dry-run', dest='outf', action='store_const', const=nullout, help="No output")
    g2.add_argument('-i', dest='inplace', action='store_const', const=True, help='In-place editing; overwrite each input file with any changes')
    g2.add_argument('-I', dest='inplace', metavar='.EXT', help='Same, but makes backups with specified extension')

    g=p.add_argument_group("Trailing space")
    multi_opt(g, '-t', '--trail-space', default='fix', help='Remove space at end-of-line (default %(default)s)')

    g=p.add_argument_group("End of file")
    multi_opt(g, '-b', '--eof-blanks', default='fix', help='Remove blank lines at end-of-file (default %(default)s)')
    multi_opt(g, '-n', '--eof-newl', default='fix', help='Ensure newline appears at end-of-file (default %(default)s)')

    g=p.add_argument_group("End of line characters")
    g.add_argument('-E', '--coerce-eol', choices=eol_name2val.keys(), action=StoreTupleAction, const='fix', default=('fix','first'),
        help='Ensure specific line endings: crlf, lf, native, or first (default is to make all line endings match the first line)')
    g.add_argument('-e', '--expect-eol', choices=eol_name2val.keys(), action=StoreTupleAction, const='report', dest='coerce_eol')
    g.add_argument('-Ie', '--ignore-eol', action='store_const', const=None, dest='coerce_eol')

    g=p.add_argument_group("Tabs and Spaces")
    multi_opt(g, '-s', '--tab-space-mix', default='report', help='Make sure no mixed spaces and/or tabs exist in leading whitespace; fix requires -x or -y SPACES (default %(default)s)')
    g2 = g.add_mutually_exclusive_group(required=False)
    g2.add_argument('-x', '--change-tabs', metavar='NS', default=None, type=int, help='Change each tab characters in leading whitespace to NS spaces.')
    g2.add_argument('-y', '--change-spaces', metavar='NS', default=None, type=int, help='Change NS consecutive spaces in leading whitespace to tab character.')

    g = p.add_mutually_exclusive_group()
    g.add_argument('-q', '--quiet', dest='verbose', action='store_const', const=0, default=1, help="Silent operation")
    g.add_argument('-v', '--verbose', action='count', help="Increasing verbosity")
    p.add_argument('-X', '--no-exit-codes', action='store_true', help="Always return 0 on success, even if issues were fixed or reported")
    args = p.parse_args()

    # Check for things that don't make sense
    if args.inplace is not None and stdin in args.inf:
        p.error("cannot use stdin for in-place editing (-i/-I); must specify filenames")
    elif args.outf not in (stdout,nullout) and len(args.inf)>1:
        p.error("cannot specify multiple input files with a single output file (-o)")

    if args.tab_space_mix=='fix' and args.change_tabs is None and args.change_spaces is None:
         args.tab_space_mix='report'
         print("changing to --report-tab-space-mix (--fix-tab-space-mix requires --change-tabs or --change-spaces)", file=stderr)

    return p, args

class FileProcessor(object):
    lre = re.compile(br'''
        (?P<ispace>\s*?)
        (?P<body>(?:\S.*?)?)
        (?P<trail>\s*?)
        (?P<eol>(?:\r\n|\n|\r|))$''', re.S | re.X)

    def __init__(self, inf, outf, actions):
        self.inf = inf
        self.outf = outf
        self.actions = actions

        # each of these a slurpy with same keys as actions,
        # representing number of issues of each type
        self.seen = slurpy((k,0) for k in actions)
        self.fixed = slurpy((k,0) for k in actions)

    # reads from .inf, writes to .outf
    # yield messages along the way: (verbosity, line, empty, message)
    def run(self):
        buffer = []
        if self.actions.coerce_eol:
            self.eol_action = self.actions.coerce_eol[0]
            self.eol_value = eol_name2val[ self.actions.coerce_eol[1] ]
        else:
            self.eol_action = self.eol_value = None
        self.linesep = None
        actions = self.actions
        seen = self.seen
        fixed = self.fixed

        if actions.tab_space_mix=='fix':
            assert actions.change_spaces or actions.change_tabs

        for ii,line in enumerate(self.inf):
            # Take the line apart
            m = self.lre.match(line)
            ispace, body, trail, eol = m.groups()
            empty = not body
            mixed_leading_whitespace = None

            # Save first EOL for matching subsequent lines to it
            if self.eol_value is None:
                self.eol_value = eol

            yield ( 4, ii+1, empty, repr(m.groups()) )

            # Detect tab/space mix
            if actions.tab_space_mix:
                if b' \t' in ispace or b'\t ' in ispace:
                    mixed_leading_whitespace = True
                    seen.tab_space_mix += 1
                    # Warn about tab/space mix
                    if actions.tab_space_mix=='report':
                        yield (0, ii+1, empty, "WARNING: mixed use of spaces and tabs at beginning of line")
                else:
                    mixed_leading_whitespace = False

            # Convert tabs to spaces
            if actions.change_tabs is not None:
                if b'\t' in ispace:
                    seen.change_tabs += 1
                    # this ensures --ignore-tab-space-mix does not replace anything
                    # and still allows normal --change-tabs operation
                    if mixed_leading_whitespace is True and actions.tab_space_mix=='fix':
                        fixed.tab_space_mix += 1
                        ispace = ispace.replace(b'\t', b' ' * actions.change_tabs)
                        fixed.change_tabs += 1
                    elif mixed_leading_whitespace is not True:
                        ispace = ispace.replace(b'\t', b' ' * actions.change_tabs)
                        fixed.change_tabs += 1
            # OR Convert spaces to tabs
            elif actions.change_spaces is not None:
                if b' ' in ispace:
                    seen.change_spaces += 1
                    if mixed_leading_whitespace is True and actions.tab_space_mix=='fix':
                        # normalize all leading whitespace to spaces first
                        ispace = ispace.replace(b'\t', b' ' * actions.change_spaces)
                        fixed.tab_space_mix += 1
                        ispace = ispace.replace(b' ' * actions.change_spaces, b'\t')
                        fixed.change_spaces += 1
                    elif mixed_leading_whitespace is not True:
                        ispace = ispace.replace(b' ' * actions.change_spaces, b'\t')
                        fixed.change_spaces += 1

            # Fix trailing space
            if actions.trail_space:
                if trail:
                    seen.trail_space += 1
                    if actions.trail_space=='fix':
                        fixed.trail_space += 1
                        trail = b''

            # Line endings (missing, matching, and coercing)
            if not eol:
                # there is no line ending...
                if actions.eof_newl:
                    seen.eof_newl += 1
                    if actions.eof_newl=='fix':
                        # ... but we want one
                        fixed.eof_newl += 1
                        if self.eol_value:
                            eol = self.eol_value
                        else:
                            self.eol_value = eol = native_eol
                            yield (0, ii+1, empty, "WARNING: don't know what line ending to add (guessed %s)" % eol_val2name.get(eol, repr(eol)))
            else:
                # there is a line ending ...
                if eol!=self.eol_value:
                    # ... but it doesn't match the expected value
                    seen.coerce_eol += 1
                    if self.eol_action=='fix':
                        fixed.coerce_eol += 1
                        eol = self.eol_value

            # Put the line back together
            outline = ispace+body+trail+eol
            if outline!=line:
                yield (3, ii+1, empty, "changing %s to %s" % (repr(line), repr(outline)))

            # empty line, could be at end of file
            if empty:
                buffer.append(outline)
            else:
                if buffer:
                    self.outf.write(b''.join(buffer))
                    buffer = []
                self.outf.write(outline)

        # handle blank lines at end
        if buffer:
            # we have leftover lines ...
            if actions.eof_blanks:
                seen.eof_blanks += len(buffer)
                if actions.eof_blanks=='fix':
                    # ... which we don't want
                    fixed.eof_blanks += len(buffer)
                    buffer = []
            self.outf.write(b''.join(buffer))

        # Quick sanity check
        for k in actions:
            ## these values are allowed not to match if --report-tab-space-mix is enabled
            if k!='change_tabs' and k!='change_spaces':
                assert fixed[k] in (seen[k],0)

# Parse arguments
p, args = parse_args()

# Actions that we're going to do
actions = slurpy((k,getattr(args,k)) for k in ('trail_space','eof_blanks','eof_newl','tab_space_mix','coerce_eol','change_tabs','change_spaces'))
all_seen = 0
all_fixed = 0

# Process files
for inf in args.inf:
    # use a temporary file for output if doing "in-place" editing
    if args.inplace is not None:
        fname = inf.name
        name,ext = os.path.splitext(os.path.basename(fname))
        try:
            # The best approach is to defer the decision about whether to keep or delete the output
            # file until *after* all processing has completed separately.
            outf = NamedTemporaryFile(dir=os.path.dirname(fname), prefix=name+'_tmp_', suffix=ext, delete=False)
        except OSError as e:
            p.error("couldn't make temp file for in-place editing: %s" % str(e))
    else:
        outf = args.outf
        if inf is stdin and outf not in (stdout, nullout):
            fname = outf.name
        else:
            fname = inf.name

    # Process one file
    fp = FileProcessor(inf, outf, actions)
    for verbose, line, empty, message in fp.run():
        if args.verbose >= verbose:
            print("%s %sLINE %d: %s" % (fname, "EMPTY " if empty else "", line, message), file=stderr)
    fixed, seen = fp.fixed, fp.seen

    # count fixes for verbose output
    problems_seen = sum( seen[k] for k in actions )
    all_seen += problems_seen
    all_fixed += sum( fixed[k] for k in actions )
    if args.verbose>=1:
        if problems_seen>0 or args.verbose>=2:
            print("%s:" % fname, file=stderr)
            if actions.trail_space:
                print("\t%s %d lines with trailing space" % ('CHOPPED' if actions.trail_space=='fix' else 'SAW', seen.trail_space), file=stderr)
            if actions.eof_blanks:
                print("\t%s %d blank lines at EOF" % ('CHOPPED' if actions.eof_blanks=='fix' else 'SAW', seen.eof_blanks), file=stderr)
            if actions.eof_newl:
                print("\t%s newline at EOF" % ('ADDED' if actions.eof_newl=='fix' and fixed.eof_newl else 'SAW MISSING' if seen.eof_newl else 'no change to'), file=stderr)
            if actions.coerce_eol:
                print("\t%s %d line endings which didn't match %s%s" % ('CHANGED' if actions.coerce_eol[0]=='fix' else 'SAW', seen.coerce_eol,
                    eol_val2name[fp.eol_value], ' from first line' if actions.coerce_eol[1]=='first' else ''), file=stderr)
            if actions.tab_space_mix:
                print("\t%s %d lines with mixed tabs/spaces" % ('CHANGED' if actions.tab_space_mix=='fix' else 'WARNED ABOUT' if actions.tab_space_mix=='report' else 'SAW', seen.tab_space_mix), file=stderr)
            if actions.change_tabs is not None:
                print("\tCHANGED tabs to %d spaces on %d lines" % (actions.change_tabs, fixed.change_tabs if fixed.change_tabs > 0 else seen.change_tabs), file=stderr)
            if actions.change_spaces is not None:
                print("\tCHANGED %d spaces to tabs on %d lines" % (actions.change_spaces, fixed.change_spaces if fixed.change_spaces > 0 else seen.change_spaces), file=stderr)

    inf.close()
    if args.inplace is not None:
        outf.close()
        if not any( fixed[k] for k in actions ):
            # delete outf if we made no changes
            os.unlink(outf.name)
        else:
            if isinstance(args.inplace, str):
                ext = args.inplace
                if os.path.exists(inf.name + ext):
                    p.error("can't make backup of %s: %s already exists" % (inf.name, inf.name + ext))
                try:
                    os.rename(inf.name, inf.name + ext)
                except OSError as e:
                    p.error("can't rename %s to %s: %s" % (inf.name, inf.name + ext, str(e)))

                shutil.copymode(inf.name + ext, outf.name)
            else:
                shutil.copymode(inf.name, outf.name)
                # replacing original file is non-atomic on Windows (rename won't work if destination exists)
                if os.name=='nt':
                    os.unlink(inf.name)

            os.rename(outf.name, inf.name)

if not args.inplace:
    outf.close()

if not args.no_exit_codes:
    if all_seen>all_fixed:
        exit(20)
    elif all_seen:
        exit(10)
