#! /usr/bin/env python3

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import unittest


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='An interactive tool to convert SVN repositories to GIT'
    )

    parser.add_argument("repo_dump_gz", help='gzipped SVN repo dump file')

    return parser.parse_args()


################################################################################

def come_up_with_repo_name(gzipped_dump_fname):
    if not gzipped_dump_fname:
        raise ValueError('Empty input')

    return os.path.basename(gzipped_dump_fname).split('.')[0]


class TestComeUpWithRepoName(unittest.TestCase):
    def test_empty_string(self):
        with self.assertRaises(ValueError):
            come_up_with_repo_name('')

    def test_nonempty_strings(self):
        testcases = [
            ('name-without-dots', 'name-without-dots'),
            ('name.with.dots', 'name'),
            ('/tmp/some.dump.gz', 'some'),
            ('../my-dump.gz', 'my-dump')
        ]

        for path, repo_name in testcases:
            result = come_up_with_repo_name(path)
            self.assertEqual(repo_name, result)

################################################################################


class Repository:
    def __init__(self, dump_fname, tmp_dir):
        self.name = come_up_with_repo_name(dump_fname)
        self.abs_path = os.path.join(tmp_dir, self.name)
        self.url = 'file://{}'.format(self.abs_path)

        subprocess.check_call(['svnadmin', 'create', self.abs_path])

    def is_standard_layout(self):
        return sorted(self.list('/')) == 'branches/ tags/ trunk/'.split()

    def list(self, path):
        return subprocess.check_output(
            ['svn', 'ls', self.url + '/' + path],
            encoding='UTF-8'
        ).splitlines()

    def restore_from_dump(self, dump_fname):
        gunzip = subprocess.Popen(['gunzip', '-c', dump_fname], stdout=subprocess.PIPE)
        svnadmin = subprocess.Popen(['svnadmin', 'load', '-q', self.abs_path], stdin=gunzip.stdout)
        svnadmin.communicate()


################################################################################

def parse_author(author_xml):
    author_tag = '<author>'
    if not author_xml.startswith(author_tag):
        raise ValueError('Not an {} element'.format(author_tag))

    v = author_xml[len(author_tag):]
    bracket_pos = v.index('<')
    return v[:bracket_pos]


class TestParseAuthor(unittest.TestCase):
    def test_empty_input(self):
        with self.assertRaises(ValueError):
            parse_author('')

    def test_regular_input(self):
        self.assertEqual('me', parse_author('<author>me</author>'))

################################################################################

def parse_svn_log_authors(log_output):
    log_lines = log_output.split('\n')
    return sorted(list(set([parse_author(s) for s in log_lines if s.startswith('<author>')])))


class TestGetAuthors(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual([], parse_svn_log_authors(''))

    def test_regular_input(self):
        log_output = """
<author>gli</author>
<date>2006-10-19T19:42:33.061832Z</date>
<author>xyz</author>
<date>2006-10-16T20:09:08.871296Z</date>
        """
        self.assertEqual(['gli', 'xyz'], parse_svn_log_authors(log_output))

    def test_repeating_authors(self):
        log_output = """
<author>gli</author>
<date>2006-10-19T19:42:33.061832Z</date>
<author>gli</author>
<date>2006-10-16T20:09:08.871296Z</date>
        """
        self.assertEqual(['gli'], parse_svn_log_authors(log_output))

    def test_output_is_sorted(self):
        log_output = """
<author>q</author>
<date>2006-10-19T19:42:33.061832Z</date>
<author>a</author>
<date>2006-10-16T20:09:08.871296Z</date>
<author>z</author>
<date>2006-10-16T20:09:08.871296Z</date>
        """
        self.assertEqual(['a', 'q', 'z'], parse_svn_log_authors(log_output))
        # TODO: test the order


################################################################################

class WorkingCopy:
    def __init__(self, tmp_dir, repo, branch):
        self.abs_path = os.path.join(tmp_dir, 'wc')

        subprocess.check_call(
            ['svn', 'co', '-q', 'file://{}/{}'.format(repo.abs_path, branch), self.abs_path]
        )

    def get_authors(self):
        log_output = subprocess.check_output(
            ['svn', 'log', '--xml', '--quiet', self.abs_path],
            encoding='UTF-8'
        )
        return parse_svn_log_authors(log_output)

################################################################################

def extract_keys_from_prompt(prompt):
    p = re.compile('\[.\]')
    matches = re.findall(p, prompt)
    if not matches:
        raise ValueError('No menu items defined')

    return ''.join([m[1] for m in matches])


class TestExtractKeysFromPrompt(unittest.TestCase):
    def test_no_keys(self):
        with self.assertRaises(ValueError):
            extract_keys_from_prompt('no keys')

    def test_with_keys(self):
        self.assertEqual('ac', extract_keys_from_prompt('Type [a] to abort, [c] to continue'))

################################################################################

def prompt_menu(prompt):
    choices = extract_keys_from_prompt(prompt).lower()

    while True:
        choice = input(prompt + ' > ').lower()
        if len(choice) == 1 and (choice in choices):
            return choice

################################################################################

def ensure_standard_repo_layout(tmp_dir, repo):
    full_wc = None

    while not repo.is_standard_layout():
        print('The top level directories in the repository are not in standard layout:')
        print(repo.list('/'))

        if not full_wc:
            print('Checking out the root of the repository to fix it...')
            full_wc = WorkingCopy(tmp_dir, repo, '/')

        print('The working copy is checked out at {}'.format(full_wc.abs_path))
        print('Fix the layout in another terminal window and make sure to commit')

        choice = prompt_menu('Type [c] to continue when done...')

    if full_wc:
        shutil.rmtree(full_wc.abs_path)


################################################################################

class AuthorsFile:
    def __init__(self, tmp_dir, repo, domain):
        wc = WorkingCopy(tmp_dir, repo, 'trunk')
        authors = wc.get_authors()
        shutil.rmtree(wc.abs_path)

        self.abs_path = os.path.join(tmp_dir, 'authors.txt')

        with open(self.abs_path, 'w') as f:
            f.write(''.join([
                '{author} = {author} <{author}@{domain}>\n'.format(author=a, domain=domain)
                for a in authors
            ]))

    def read(self):
        return open(self.abs_path).read()

################################################################################

class GitRepo:

    def __init__(self, tmp_dir):
        self.abs_path = os.path.join(tmp_dir, 'git')

    def branch(self, name, what):
        subprocess.check_call(['git', 'branch', name, what], cwd=self.abs_path)

    def delete_branch(self, branch, remote=False):
        remote_arg = ['-r'] if remote else []
        subprocess.check_call(['git', 'branch', '-D'] + remote_arg + [branch], cwd=self.abs_path)

    def get_short_refs(self, refs=None):
        cmdline = ['git', 'for-each-ref', '--format=%(refname:short)']
        if refs:
            cmdline.append(refs)

        return subprocess.check_output(cmdline, cwd=self.abs_path, encoding='UTF-8').splitlines()

    def tag(self, tag_name, what):
        subprocess.check_call(['git', 'tag', tag_name, what], cwd=self.abs_path)

################################################################################

def generate_authors_file(tmp_dir, repo):
    domain = input('Which domain to assign users to? > ')

    authors_file = AuthorsFile(tmp_dir, repo, domain)

    while True:
        choice = prompt_menu('Generated authors list, [e]dit, [p]roceeed or [v]iew?')

        if choice == 'e':
            subprocess.check_call([os.environ['EDITOR'], authors_file.abs_path])
        elif choice == 'p':
            break
        elif choice == 'v':
            print(authors_file.read())

    return authors_file


def convert_svn_to_git(authors_file, git, repo):
    subprocess.check_call([
        'git', 'svn', 'clone', repo.url, '--authors-file={}'.format(authors_file.abs_path),
        '--no-metadata', '--prefix', '', '-s', git
    ])


def fix_tag_name(tag):
    tag_prefix = 'tags/'
    if tag.startswith(tag_prefix):
        return tag[len(tag_prefix):]
    else:
        return tag


def main():
    args = parse_arguments()

    print('Using SVN repository dump {}'.format(args.repo_dump_gz))

    tmp_dir = tempfile.mkdtemp()
    print("Intermediate results will be stored in {}".format(tmp_dir))

    repo = Repository(args.repo_dump_gz, tmp_dir)
    print('Suggested repository name: {}'.format(repo.name))

    print('Restoring the SVN repository from the dump...')
    repo.restore_from_dump(args.repo_dump_gz)

    ensure_standard_repo_layout(tmp_dir, repo)

    authors_file = generate_authors_file(tmp_dir, repo)

    git_repo = GitRepo(tmp_dir)

    print('Converting to GIT repository at {}'.format(git_repo.abs_path))
    convert_svn_to_git(authors_file, git_repo.abs_path, repo)

    for tag in git_repo.get_short_refs('refs/remotes/tags'):
        git_repo.tag(fix_tag_name(tag), tag)
        git_repo.delete_branch(tag, remote=True)

    for branch in git_repo.get_short_refs('refs/remotes'):
        git_repo.branch(branch, 'refs/remotes/' + branch)
        git_repo.delete_branch(branch, remote=True)

    for branch in git_repo.get_short_refs():
        if '@' in branch:
            git_repo.delete_branch(branch, remote=True)

    git_repo.delete_branch('trunk', remote=False)

    print(tmp_dir)

    # TODO: remove temporary directory


if __name__ == '__main__':
    main()
