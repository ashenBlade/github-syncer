import errno
import os
import subprocess as proc
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
import argparse

from github import Github, Repository

logger = logging.getLogger()

class Git:
    @staticmethod
    def is_git_repo(dir: str) -> bool:
        return os.path.exists(os.path.join(dir, '.git'))

    @staticmethod
    def pull(repo_dir: str):
        proc.run(['git', 'pull'], cwd=repo_dir, stdout=proc.PIPE, stderr=proc.PIPE)
    
    @staticmethod
    def clone(repo_dir: str, url: str):
        proc.run(['git', 'clone', url, repo_dir], stdout=proc.PIPE, stderr=proc.PIPE)

class LocalRepository:
    gh: Github
    repo: Repository
    path: str
    def __init__(self, gh: Github, repo: Repository, path: str):
        self.gh = gh
        self.repo = repo
        self.path = path

    def init(self):
        if os.path.exists(self.path):
            if not os.path.isdir(self.path):
                raise RuntimeError(f'repo dir {self.path} must be directory')
            if not Git.is_git_repo(self.path):
                raise RuntimeError(f'repo dir {self.path} is not a git repository')
        else:
            logger.info(f'cloning repository %s', self.repo.clone_url)
            Git.clone(self.path, self.repo.clone_url)
    
    def update(self):
        Git.pull(self.path)
    
    def __eq__(self, value):
        return value.repo.id == self.repo.id
    
    def __hash__(self):
        return self.repo.id

class GithubSync:
    repos_dir: str
    gh: Github
    local_repos: set[LocalRepository]

    def __init__(self, github):
        self.gh = github
        self.repos_dir = os.path.join(os.getcwd(), 'repos')
        if not os.path.exists(self.repos_dir):
            os.mkdir(self.repos_dir)

        self._init_repos()

    def _init_repos(self):
        self.local_repos = set()

        for repo in self.gh.get_user().get_repos():
            local_repo = LocalRepository(self.gh, repo, os.path.join(self.repos_dir, repo.name))
            local_repo.init()

    def _find_new_repos(self):
        remote_repos = {
            LocalRepository(self.gh, repo, os.path.join(self.repos_dir, repo.name))
            for repo
            in self.gh.get_user().get_repos()
        }
        new_repos = remote_repos.difference(self.local_repos)
        deleted_repos = self.local_repos.difference(remote_repos)

        for repo in new_repos:
            repo.init()
            self.local_repos.add(repo)

        for repo in deleted_repos:
            self.local_repos.remove(repo)

    def sync(self):
        self._find_new_repos()
        for repo in self.local_repos:
            repo.update()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token-file', type=str, required=True, help='File with GitHub token')
    parser.add_argument('--update-delay', type=int, default=3600, help='Delay between git checks')
    parser.add_argument('--log-file', type=str, help='File name of log file')
    parser.add_argument('--work-dir', type=str, help='Directory to store data in')
    args = parser.parse_args()

    try:
        with open(args.token_file, 'r') as token_file:
            token = token_file.read().strip()

    except IOError as io:
        if io.errno == errno.ENOENT:
            logger.error('provided token file does not exists')
            sys.exit(1)
        else:
            logger.error('failed to read token file', exc_info=io)
            sys.exit(1)

    delay = args.update_delay
    if delay < 0:
        logger.error('invalid update delay value - can not be negative. given: %i', delay)
        return
    
    handlers = None
    if args.log_file:
        handlers = [RotatingFileHandler(args.log_file, maxBytes=1024 * 1024, delay=True, backupCount=5)]
    
    if args.work_dir:
        try:
            os.chdir(args.work_dir)
        except OSError as e:
            logger.error('failed to change directory to %s', args.work_dir, exc_info=e)
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s]: %(message)s', handlers=handlers)

    logger.info('initializing github syncer')
    syncer = GithubSync(Github(token))
    while True:
        try:
            syncer.sync()
        except TimeoutError:
            logger.warning('timeout exceeded during repos update')

        time.sleep(delay)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error('Unhandled exception during work', exc_info=e)
        sys.exit(1)
