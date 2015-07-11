#!/usr/bin/env python2
from __future__ import print_function
import argparse
from sys import stdin, stdout, stderr, exit
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
    g2.add_argument('-0', '--dry-run', dest='outf', action='store_const', const=open(os.devnull, 'wb'), help="No output")
    g2.add_argument('-i', dest='inplace', action='store_const', const=True, help='In-place editing; overwrite each input file with any changes')
    g2.add_argument('-I', dest='inplace', metavar='.EXT', help='Same, but makes backups with specified extension')

    g=p.add_argument_group("Trailing space")
    multi_opt(g, '-t', '--trail-space', default='fix', help='Remove space at end-of-line (default %(default)s)')

    g=p.add_argument_group("End of file")
    multi_opt(g, '-b', '--eof-blanks', default='fix', help='Remove blank lines at end-of-file (default %(default)s)')
    multi_opt(g, '-n', '--eof-newl', default='fix', help='Ensure newline appears at end-of-file (default %(default)s)')

    g=p.add_argument_group("End of line characters")
    multi_opt(g, '-m', '--match-eol', default='fix', help='Make sure all lines match the first line (default %(default)s)')
    g.add_argument('-E', '--coerce-eol', action='store', metavar='ENDING', choices=('crlf','lf','native','none'), default='none',
                   help='Coerce line endings to a specific type: crlf, lf, or native (default %(default)s)');

    g=p.add_argument_group("Tabs and Spaces")
    multi_opt(g, '-s', '--tab-space-mix', default='report', help='Make sure no mixed spaces and/or tabs exist in leading whitespace; fix requires -x or -y NS spaces (default %(default)s)')
    g2 = g.add_mutually_exclusive_group(required=False)
    g2.add_argument('-x', '--change-tabs', metavar='NS', default=None, type=int, help='Change each tab characters in leading whitespace to NS spaces.')
    g.add_argument('-a', '--change-non-leading-tabs', metavar='NS', default=None, type=int, help='Change each tab characters after non blanks to NS spaces.')
    g2.add_argument('-y', '--change-spaces', metavar='NS', default=None, type=int, help='Change NS consecutive spaces in leading whitespace to tab character.')

    g = p.add_mutually_exclusive_group()
    g.add_argument('-q', '--quiet', dest='verbose', action='store_const', const=0, default=1, help="Silent operation")
    g.add_argument('-v', '--verbose', action='count', help="Increasing verbosity")
    p.add_argument('-X', '--no-exit-codes', action='store_true', help="Always return 0 on success, even if issues were fixed or reported")
    args = p.parse_args()

    # Check for things that don't make sense
    if args.inplace is not None and stdin in args.inf:
        p.error("cannot use stdin for in-place editing (-i/-I); must specify filenames")
    elif args.outf!=stdout and len(args.inf)>1:
        p.error("cannot specify multiple input files with a single output file (-o)")

    if args.tab_space_mix=='fix' and args.change_tabs is None and args.change_spaces is None:
         args.tab_space_mix='report'
         print("changing to --report-tab-space-mix (--fix-tab-space-mix requires --change-tabs or --change-spaces)", file=stderr)

    return p, args

class FileProcessor(object):
    lre = re.compile(r'''
        (?P<ispace>\s*?)
        (?P<body>(?:\S.*?)?)
        (?P<trail>\s*?)
        (?P<eol>(?:\r\n|\n|\r|))$''', re.S | re.X)

    def __init__(self, inf, outf, actions, coerce_eol):
        self.inf = inf
        self.outf = outf
        self.actions = actions
        self.coerce_eol = coerce_eol

        # each of these a slurpy with same keys as actions,
        # representing number of issues of each type
        self.seen = slurpy((k,0) for k in actions)
        self.fixed = slurpy((k,0) for k in actions)

    # reads from .inf, writes to .outf
    # yield messages along the way: (verbosity, line, empty, message)
    def run(self):
        buffer = []
        self.first_eol = None
        self.linesep = None
        actions = self.actions
        seen = self.seen
        fixed = self.fixed

        if actions.tab_space_mix=='fix':
            assert actions.change_spaces or actions.change_tabs

        for ii,line in enumerate(self.inf):
            # Take the line apart, and save first EOL for matching subsequent lines to it
            m = self.lre.match(line)
            ispace, body, trail, eol = m.groups()
            empty = not body
            mixed_leading_whitespace = None
            if self.first_eol is None:
                self.first_eol = eol
                self.linesep = {'\r\n':'crlf','\n':'lf'}.get(eol, repr(eol))

            yield ( 4, ii+1, empty, repr(m.groups()) )

            # Detect tab/space mix
            if actions.tab_space_mix:
                if ' \t' in ispace or '\t ' in ispace:
                    mixed_leading_whitespace = True
                    seen.tab_space_mix += 1
                    # Warn about tab/space mix
                    if actions.tab_space_mix=='report':
                        yield (0, ii+1, empty, "WARNING: mixed use of spaces and tabs at beginning of line")
                else:
                    mixed_leading_whitespace = False

            # Convert tabs to spaces
            if actions.change_tabs is not None:
                if '\t' in ispace:
                    seen.change_tabs += 1
                    # this ensures --ignore-tab-space-mix does not replace anything
                    # and still allows normal --change-tabs operation
                    if mixed_leading_whitespace is True and actions.tab_space_mix=='fix':
                        fixed.tab_space_mix += 1
                        ispace = ispace.replace('\t', ' ' * actions.change_tabs)
                        fixed.change_tabs += 1
                    elif mixed_leading_whitespace is not True:
                        ispace = ispace.replace('\t', ' ' * actions.change_tabs)
                        fixed.change_tabs += 1
            # OR Convert spaces to tabs
            elif actions.change_spaces is not None:
                if ' ' in ispace:
                    seen.change_spaces += 1
                    if mixed_leading_whitespace is True and actions.tab_space_mix=='fix':
                        # normalize all leading whitespace to spaces first
                        ispace = ispace.replace('\t', ' ' * actions.change_spaces)
                        fixed.tab_space_mix += 1
                        ispace = ispace.replace(' ' * actions.change_spaces, '\t')
                        fixed.change_spaces += 1
                    elif mixed_leading_whitespace is not True:
                        ispace = ispace.replace(' ' * actions.change_spaces, '\t')
                        fixed.change_spaces += 1

            if body and actions.change_non_leading_tabs is not None:
                if '\t' in body:
                    body = body.replace('\t', ' ' * actions.change_non_leading_tabs)
                    seen.change_non_leading_tabs += 1
                    fixed.change_non_leading_tabs += 1

            # Fix trailing space
            if actions.trail_space:
                if trail:
                    seen.trail_space += 1
                    if actions.trail_space=='fix':
                        fixed.trail_space += 1
                        trail = ''

            # Line endings (missing, matching, and coercing)
            if not eol:
                # there is no line ending...
                if actions.eof_newl:
                    seen.eof_newl += 1
                    if actions.eof_newl=='fix':
                        # ... but we want one
                        fixed.eof_newl += 1
                        if coerce_eol:
                            eol = coerce_eol
                        elif self.first_eol:
                            eol = self.first_eol
                        else:
                            eol = os.linesep
                            self.linesep = {'\r\n':'crlf','\n':'lf'}.get(eol, repr(eol))
                            yield (0, ii+1, empty, "WARNING: don't know what line ending to add (guessed %s)" % self.linesep)
            else:
                # there is a line ending...
                if eol!=self.first_eol:
                    if actions.match_eol:
                        # ... but it doesn't match first line
                        seen.match_eol += 1
                        if actions.match_eol=='fix':
                            fixed.match_eol += 1
                            eol = self.first_eol

                # Line endings (coercing)
                if eol!=coerce_eol and coerce_eol:
                    # ... but it isn't the one we want to force
                    eol = coerce_eol
                    seen.coerce_eol += 1
                    fixed.coerce_eol += 1

            # Put the line back together
            outline = ispace+body+trail+eol
            if outline!=line:
                yield (3, ii+1, empty, "changing %s to %s" % (repr(line), repr(outline)))

            # empty line, could be at end of file
            if empty:
                buffer.append(outline)
            else:
                if buffer:
                    outf.write(''.join(buffer))
                    buffer = []
                outf.write(outline)

        # handle blank lines at end
        if buffer:
            # we have leftover lines ...
            if actions.eof_blanks:
                seen.eof_blanks += len(buffer)
                if actions.eof_blanks=='fix':
                    # ... which we don't want
                    fixed.eof_blanks += len(buffer)
                    buffer = []
            outf.write(''.join(buffer))

        # Quick sanity check
        for k in actions:
            # these values are allowed not to match if --report-tab-space-mix is enabled
            if k!='change_tabs' and k!='change_spaces':
                assert fixed[k] in (seen[k],0)

# Parse arguments
p, args = parse_args()

# Actions that we're going to do
actions = slurpy((k,getattr(args,k)) for k in ('trail_space','eof_blanks','eof_newl','match_eol','coerce_eol','tab_space_mix','change_tabs','change_non_leading_tabs','change_spaces'))
coerce_eol = dict(crlf='\r\n',lf='\n',native=os.linesep,none=None)[args.coerce_eol]
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
            # file until *after* all processing has completed separately. Unfortunately, this doesn't
            # work on NT where setting NamedTemporaryFile.delete does nothing after the initial
            # creation of the file.
            delete = False if os.name=='nt' else True
            outf = NamedTemporaryFile(dir=os.path.dirname(fname), prefix=name+'_tmp_', suffix=ext, delete=delete)
        except OSError as e:
            p.error("couldn't make temp file for in-place editing: %s" % str(e))
    else:
        outf = args.outf
        if inf is stdin and outf is not stdout:
            fname = outf.name
        else:
            fname = inf.name

    # Process one file
    fp = FileProcessor(inf, outf, actions, coerce_eol)
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
            if actions.match_eol:
                print("\t%s %d line endings which didn't match %s from first line" % ('CHANGED' if actions.match_eol=='fix' else 'SAW', seen.match_eol, fp.linesep), file=stderr)
            if coerce_eol:
                print("\tCOERCED %d line endings to %s" % (seen.coerce_eol, actions.coerce_eol), file=stderr)
            if actions.tab_space_mix:
                print("\t%s %d lines with mixed tabs/spaces" % ('CHANGED' if actions.tab_space_mix=='fix' else 'WARNED ABOUT' if actions.tab_space_mix=='report' else 'SAW', seen.tab_space_mix), file=stderr)
            if actions.change_tabs is not None:
                print("\tCHANGED tabs to %d spaces on %d lines" % (actions.change_tabs, fixed.change_tabs if fixed.change_tabs > 0 else seen.change_tabs), file=stderr)
            if actions.change_non_leading_tabs is not None:
                print("\tCHANGED tabs to %d spaces on %d lines after non blanks" % (actions.change_non_leading_tabs, fixed.change_non_leading_tabs if fixed.change_non_leading_tabs > 0 else seen.change_non_leading_tabs), file=stderr)
            if actions.change_spaces is not None:
                print("\tCHANGED %d spaces to tabs on %d lines" % (actions.change_spaces, fixed.change_spaces if fixed.change_spaces > 0 else seen.change_spaces), file=stderr)

    inf.close()
    if args.inplace is not None:
        if not any( fixed[k] for k in actions ):
            # let outf get auto-deleted if we made no changes
            outf.close()
            if os.name=='nt':
                os.unlink(outf.name)
        else:
            if isinstance(args.inplace, basestring):
                ext = args.inplace
                if os.path.exists(inf.name + ext):
                    p.error("can't make backup of %s: %s already exists" % (inf.name, inf.name + ext))
                try:
                    os.rename(inf.name, inf.name + ext)
                except OSError as e:
                    p.error("can't rename %s to %s: %s" % (inf.name, inf.name + ext, str(e)))

                # don't mark output file with delete=False until all the preceding steps have succeeded
                outf.delete = False
                outf.close()
                shutil.copymode(inf.name + ext, outf.name)
            else:
                outf.delete = False
                outf.close()
                shutil.copymode(inf.name, outf.name)
                # rename won't work on Windows if destination exists
                os.unlink(inf.name)

            os.rename(outf.name, inf.name)

if not args.inplace:
    outf.close()

if not args.no_exit_codes:
    if all_seen>all_fixed:
        exit(20)
    elif all_seen:
        exit(10)
