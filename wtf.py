#!/usr/bin/env python2
import argparse
from sys import stdin, stdout, stderr
import re
import os
import shutil
try:
    import cStringIO as StringIO
except ImportError:
    import StringIO

def binopt(p, *args, **kw):
    values = kw.pop('values', (True,False))
    prefix2 = kw.pop('prefix2', 'no')

    g=p.add_mutually_exclusive_group(required=False)

    # first option (typically yes/store_true)
    d = dict(kw)
    d['action']='store_const'
    d['const']=values[0]
    o1 = g.add_argument(*args, **d)

    # mangle the flags for the second option
    #   '-a', '--allow' becomes '-A', '--no-allow'
    a2 = []
    for a in args:
        if a.startswith('--'):
            a2.append('--%s-%s' % (prefix2, a[2:]))
        elif a.startswith('-'):
            if a.upper()!=a:
                a2.append(a.upper())
            elif a.lower()!=a:
                a2.append(a.lower())
            else:
                raise ValueError
        else:
            a2.append(a)

    # second option (typically no/store_false)
    d['action']='store_const'
    d['const']=values[1]

    # same destination, no help, no default
    d['dest'] = o1.dest
    d.pop('help')
    d.pop('default')
    g.add_argument(*a2, **d)

    return g

# Parse arguments
p = argparse.ArgumentParser(description='Removes all manner of annoying whitespace errors from text files.')
g=p.add_argument_group("Input/output modes:")
g.add_argument('inf', metavar="textfile", nargs='*', type=argparse.FileType('rb'), help='Input file(s)', default=[stdin])
g.add_argument('-o', metavar="outfile", dest='outf', type=argparse.FileType('w'), help='Output file (default is stdout)', default=stdout)
g2=g.add_mutually_exclusive_group(required=False)
g2.add_argument('-i', dest='inplace', action='store_const', const=True, help='In-place editing; allows multiple input files')
g2.add_argument('-I', dest='inplace', metavar='.EXT', help='Same, but makes backups with specified extension')

g=p.add_argument_group("Errors to fix:")
binopt(g, '-t', '--trail-space', default=True, help='Remove space at end-of-line (default %(default)s)')
binopt(g, '-b', '--eof-blanks', default=True, help='Remove blank lines at end-of-file (default %(default)s)')
binopt(g, '-n', '--eof-newl', default=True, help='Ensure newline appears at end-of-file (default %(default)s)')
binopt(g, '-m', '--tab-space-mix', default=True, help='Warn if spaces followed by tabs in whitespace at beginning of line (default %(default)s)')
g2=binopt(g, '-e', '--same-eol', default=True, help='Make sure all line match the first line (default %(default)s)')
g2.add_argument('-F', '--force-eol', action='store', metavar='ENDING', choices=('crlf','lf','ignore'), default='ignore', help='Force line endings to crlf or lf (default %(default)s)');

p.add_argument('-v', '--verbose', action='count', default=0, help="Increasing verbosity");
args = p.parse_args()

# Check for things that don't make sense
if args.inplace is not None and stdin in args.inf:
    p.error("cannot use stdin for in-place editing (-i/-I); must specify filenames")
elif args.outf!=stdout:
    if args.inplace is not None:
        p.error("cannot specify both in-place editing (-i/-I) and output file (-o)")
    elif len(args.inf)>1:
        p.error("cannot specify multiple input files with a single output file (-o)")

force_eol = dict(crlf='\r\n',lf='\n',ignore=None)[args.force_eol]

lre = re.compile(r'''
    (?P<ispace>\s*?)
    (?P<body>(?:\S.*?)?)
    (?P<trail>\s*?)
    (?P<eol>(?:\r\n|\n|\r|))$''', re.S | re.X)

# Process files
for inf in args.inf:
    outlines = []
    fix_trail = fix_eof_blanks = coerce_eol = match_eol = fix_eof_newl = warn_tsmix = 0
    first_eol = linesep = None

    # use in-memory copy for inplace
    if args.inplace is not None:
        fname = inf.name
        orig = inf
        inf = StringIO.StringIO(inf.read())
        orig.close()
        # make backup if desired
        if isinstance(args.inplace, basestring):
            try:
                shutil.copyfile(fname, fname + args.inplace)
            except shutil.Error:
                p.error("could not make backup copy of %s as %s" % (fname, fname + args.inplace))
        outf = open(fname, "wb")
    else:
        outf = args.outf
        if inf is stdin and outf is not stdout:
            fname = outf.name
        else:
            fname = inf.name


    for ii,line in enumerate(inf):
        # Decompose the line into parts
        m = lre.match(line)
        ispace, body, trail, eol = m.groups()
        empty = not body

        # Save first EOL for matching subsequent lines to it
        if first_eol is None:
            first_eol = eol
            linesep = {'\r\n':'crlf','\n':'lf'}.get(eol, repr(eol))

        if args.verbose>=4:
            print>>stderr, "%s %sLINE %d: %s" % (fname, 'EMPTY ' if empty else '', ii+1, repr(m.groups()))

        # Warn about tab/space mix
        if ' \t' in ispace:
            warn_tsmix += 1
            if args.tab_space_mix:
                print>>stderr, "%s %sLINE %d: WARNING: spaces followed by tabs in whitespace at beginning of line" % (fname, 'EMPTY ' if empty else '', ii+1)

        # Fix trailing space
        if trail:
            fix_trail += 1
            if args.trail_space:
                trail = ''

        if not eol:
            # there is no line ending...
            fix_eof_newl += 1
            if args.eof_newl:
                # ... but we want one
                if force_eol:
                    eol = force_eol
                elif first_eol:
                    eol = first_eol
                else:
                    eol = os.linesep
                    linesep = {'\r\n':'crlf','\n':'lf'}.get(eol, repr(eol))
                    print>>stderr, "%s %sLINE %d: WARNING: don't know what line ending to add (guessed %s)" % (fname, 'EMPTY ' if empty else '', ii+1, linesep)
            else:
                # ... and we don't care
                pass
        else:
            # there is a line ending...
            if force_eol and eol!=force_eol:
                # ... but it's not the one we want to force
                eol = force_eol
                coerce_eol += 1
            elif eol!=first_eol:
                # ... but it's not consistent
                match_eol += 1
                if args.same_eol:
                    eol = first_eol
            else:
                # ... it's right
                pass

        outline = ispace+body+trail+eol

        if args.verbose>=3 and outline!=line:
            print>>stderr, "%s %sLINE %d: changing %s to %s" % (fname, 'EMPTY ' if empty else '', ii+1, repr(line), repr(outline))

        # empty line, could be at end of file
        if empty:
            outlines.append(outline)
        else:
            if outlines:
                outf.write(''.join(outlines))
                outlines = []
            outf.write(outline)

    # cleanup extra blank lines
    if outlines:
        # we have leftover lines ...
        if args.eof_blanks:
            # ... which we don't want
            fix_eof_blanks += len(outlines)
        else:
            # ... which we will keep
            outf.write(''.join(outlines))

    if args.inplace:
        outf.close()

    # count fixes for verbose output
    if args.verbose>=1:
        problems = fix_trail + fix_eof_blanks + fix_eof_newl + coerce_eol + match_eol + warn_tsmix
        if problems>0 or args.verbose>=2:
            print>>stderr, "%s:" % fname
            print>>stderr, "\t%s %d lines with trailing space" % ('CHOPPED' if args.trail_space else 'IGNORED', fix_trail)
            print>>stderr, "\t%s %d blank lines at EOF" % ('CHOPPED' if args.eof_blanks else 'IGNORED', fix_eof_blanks)
            print>>stderr, "\t%s newline at EOF" % ('ADDED' if args.eof_newl and fix_eof_newl else 'IGNORED missing' if fix_eof_newl else 'no change to')
            if args.force_eol != 'ignore':
                print>>stderr, "\tCOERCED %d line endings to %s" % (coerce_eol, args.force_eol)
            else:
                print>>stderr, "\t%s %d line endings which don't match %s from first line" % ('CHANGED' if args.same_eol else 'IGNORED', match_eol, linesep)
            print>>stderr, "\t%s %d lines with tabs/spaces mix" % ('warned about' if args.tab_space_mix else 'IGNORED', warn_tsmix)
