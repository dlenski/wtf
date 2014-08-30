Whitespace Total Fixer
======================

Identifies and/or fixes inconsistent whitespace and line endings in
text files, so that they don't clog up your commits to version control
systems like Git, Mercurial, or Subversion.

How to use it (see below for options to control exactly which
whitespace issues it fixes):

     # consistent whitespace from programs that generate text files
     text_file_generator | wtf.py -o clean_output.txt

     # in-place editing
     wtf.py -i file1.txt file2.txt file3.txt
     wtf.py -I.bak file1.txt file2.txt file3.txt # ditto, with backups

     # exit status
     wtf.py file1.txt file2.txt file3.txt > /dev/null
     if (( $? == 10 )); then
     	echo "isses fixed"
     elif (( $? == 20 )); then
        echo "unfixed issues!"
     fi

Why should you use it:

* It's like an incomprehensible `sed -e 's/LINENOISE/'` one liner but way better.
* It's similar to [`git
  stripspace`](https://www.kernel.org/pub/software/scm/git/docs/git-stripspace.html)
  but more flexible and detailed.
* `wtf.py` is a Python2 script (tested with Python 2.7.5) with *no
  dependencies beyond the standard Python library*.

Exciting origin story
---------------------

One day at work I spent way too much time dealing wrangling commits
laden with whitespace tools generate from various recalcitrant editing
tools on multiple platforms. That evening, I went home and spent way
too much time writing this program.

Whitespace issues addressed
---------------------------

WTF current fixes or detects a few common types of whitespace
issues. Most of these options have three possible values enabling the
user to fix, report, or ignore the corresponding whitespace issue.

Remove trailing spaces at the ends of lines (default is **fix**):

        -t, --trail-space
        -T, --report-trail-space
        -It, --ignore-trail-space

Remove blank lines at the ends of files (default is **fix**):

        -b, --eof-blanks
        -B, --report-eof-blanks
        -Ib, --ignore-eof-blanks

Ensure that a new-line character appears at the end of the file (default is **fix**):

        -n, --eof-newl
        -N, --report-eof-newl
        -In, --ignore-eof-newl

Make sure that all lines have matching EOL markers (that is, no
mixing of lf/crlf). Default is to **fix** non-matching EOL characters
by making them all the same as the first line of the file. The desired
EOL markers can also be set to a specific value (lf/crlf/native), in
which case all lines will unconditionally receive this marker.

        -m, --match-eol
        -M, --report-match-eol
        -Im, --ignore-match-eol
        -E ENDING, --coerce-eol ENDING

Check for spaces followed by tabs in the whitespace at the beginning
of a line; no strategy for fixing this condition is currently
implemented, so the default is to **warn**:

        -s, --tab-space-mix
        -Is, --ignore-tab-space-mix

Reporting
---------

With the `-v` option, WTF will summarize each file processed, if
issues were found and/or fixed. With `-vv` it will also report
issue-free files.

    nightmare.txt:
    	CHOPPED 1 lines with trailing space
    	CHOPPED 0 blank lines at EOF
    	ADDED newline at EOF
    	CHANGED 1 line endings which didn't match crlf from first line
    	WARNED ABOUT 1 lines with tabs/spaces mix

WTF will return the following return codes on successfull operation:

* 0: no issues seen (or `-X`/`--no-exit-codes` specified)
* 10: issues fixed
* 20: unfixed issues seen

Todo
----

* Stability tests?
* Tab/space conversion?
* Unicode tests?

Bugs
----
Corrupts source code files written in the [Whitespace programming language](https://en.wikipedia.org/wiki/Whitespace_(programming_language)).

Anything else?

Author
------
&copy; Daniel Lenski <<dlenski@gmail.com>> (2014-)

License
-------
[GPL v3 or later](http://www.gnu.org/copyleft/gpl.html)
