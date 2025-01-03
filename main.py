import errno
import os
import subprocess as proc
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
import argparse

from github import Github, Repository, Event
import requests

logger = logging.getLogger()

class GitError(Exception):
    msg: str

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg
    
    @classmethod
    def from_error_msg(cls, stderr: bytes):
        try:
            fatal = next(
                str for str in
                stderr.decode('utf-8', 'replace').split('\n')
                if str.startswith('fatal:')
            )
        except StopIteration:
            return cls(stderr)
        except UnicodeDecodeError:
            return cls('unknown error')

        if 'not a git repository' in fatal:
            return NotRepositoryError(fatal)
        if 'already exists and is not an empty' in fatal:
            return RepositoryAlreadyExistsError(fatal)
        if ('unable to access'                      in fatal or
            'Could not read from remote repository' in fatal or
            'unable to look up '                    in fatal or
            'early EOF'                             in fatal):
            return GitConnectionError(fatal)

        return cls(stderr)

class NotRepositoryError(GitError):
    pass

class RepositoryAlreadyExistsError(GitError):
    pass

class GitConnectionError(GitError):
    pass

class Git:
    _env = {'LC_MESSAGES': 'en_US.UTF-8'}
    
    @staticmethod
    def is_git_repo(dir: str) -> bool:
        return os.path.exists(os.path.join(dir, '.git'))

    @staticmethod
    def pull(repo_dir: str):
        result = proc.run(['git', 'pull'], cwd=repo_dir, env=Git._env,
                          stdout=proc.PIPE, stderr=proc.PIPE)
        if result.returncode != 0:
            raise GitError.from_error_msg(result.stderr)

    @staticmethod
    def clone(repo_dir: str, url: str):
        result = proc.run(['git', 'clone', url, repo_dir], env=Git._env, 
                          stdout=proc.PIPE, stderr=proc.PIPE)
        if result.returncode != 0:
            raise GitError.from_error_msg(result.stderr)
        
    @staticmethod
    def has_any_branches(repo_dir: str):
        result = proc.run(['git', 'branch'], cwd=repo_dir, env=Git._env, 
                          stdout=proc.PIPE, stderr=proc.PIPE)
        if result.returncode != 0:
            raise GitError.from_error_msg(result.stderr)

        return any(l for l in result.stdout.splitlines()
                   if l.decode('utf-8', 'replace').strip())

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
            logger.info('cloning repository %s', self.repo.clone_url)
            Git.clone(self.path, self.repo.clone_url)
    
    def update(self):
        logger.debug('updating repository %s', self.repo.name)
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
        self.local_repos = set()

    def init(self):
        if not os.path.exists(self.repos_dir):
            os.mkdir(self.repos_dir)

        logger.info('initializing github syncer')
        try:
            self._init_repos()
        except GitConnectionError as exc:
            logger.warning('failed to initialize repositories at start - network is not available', exc_info=exc)
        except requests.ConnectionError as exc:
            logger.warning('failed to initialize repositories at start - network is not available', exc_info=exc)

    def _init_repos(self):
        self.local_repos = set()
        user = self.gh.get_user()
        for repo in user.get_repos():
            # Get only user's repos. i.e. skip orgs
            if repo.owner.id != user.id:
                continue

            repo_path = os.path.join(self.repos_dir, repo.name)
            local_repo = LocalRepository(self.gh, repo, repo_path)
            try:
                local_repo.init()
            except RepositoryAlreadyExistsError:
                # This should not happen, because of checks
                # but anyway not a fatal error
                pass

            self.local_repos.add(local_repo)

    def _find_new_repos(self):
        user = self.gh.get_user()
        remote_repos = {
            LocalRepository(self.gh, repo, os.path.join(self.repos_dir, repo.name))
            for repo
            in user.get_repos()
            if repo.owner.id == user.id
        }
        new_repos = remote_repos.difference(self.local_repos)
        deleted_repos = self.local_repos.difference(remote_repos)

        for repo in new_repos:
            logger.info('start tracking repository %s', repo.repo.name)
            repo.init()
            self.local_repos.add(repo)

        for repo in deleted_repos:
            logger.info('stop tracking repository %s', repo.repo.name)
            self.local_repos.remove(repo)

    def sync(self):
        self._find_new_repos()
        for repo in self.local_repos:
            try:
                repo.update()
            except GitError as git_error:
                if ('configuration specifies to merge with' in git_error.msg and 
                    not Git.has_any_branches(repo.path)):
                    # There may be empty repositories - they do not have any branches.
                    # 'git pull' causes such error.
                    pass
                else:
                    raise git_error

def is_git_installed():
    out = proc.run(['git', '--version'], stdout=proc.PIPE, stderr=proc.PIPE)
    return out.returncode == 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token-file', type=str, required=True, help='File with GitHub token')
    parser.add_argument('--update-delay', type=int, default=60, help='Delay between git checks')
    parser.add_argument('--log-file', type=str, help='File name of log file')
    args = parser.parse_args()

    if args.token_file:
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
    else:
        try:
            token = os.environ['GHSYNCER_TOKEN']
        except KeyError:
            logger.error('token file is not specified and GHSYNCER_TOKEN env is not set')
            sys.exit(1)

    delay = args.update_delay
    if delay < 0:
        logger.error('invalid update delay value - can not be negative. given: %i', delay)
        sys.exit(1)

    handlers = None
    if args.log_file:
        handlers = [RotatingFileHandler(args.log_file, maxBytes=1024 * 1024, delay=True, backupCount=5)]
    
    if not is_git_installed():
        logger.error('could not detect git is installed on system')
        sys.exit(1)
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s]: %(message)s', handlers=handlers)

    syncer = GithubSync(Github(token))
    try:
        syncer.init()
    except Exception as e:
        logger.error('failed to initialize repository', exc_info=e)
        sys.exit(1)

    logger.info('synchronization is starting')
    while True:
        time.sleep(delay)

        try:
            syncer.sync()
        except TimeoutError as exc:
            logger.warning('timeout exceeded during repos update', exc_info=exc)
        except requests.exceptions.ConnectionError as exc:
            logger.warning('timeout exceeded for connection attempt', exc_info=exc)
        except GitConnectionError as exc:
            logger.warning('repository is unavailable', exc_info=exc)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error('Unhandled exception during work', exc_info=e)
        sys.exit(1)
