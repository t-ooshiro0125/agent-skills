"""Git を使うテストを、呼び出し元のリポジトリ設定から隔離する。"""
import os
from unittest.mock import patch


def isolate_git_environment(test_case):
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith('GIT_')}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    isolation = patch.dict(os.environ, environment, clear=True)
    isolation.start()
    test_case.addCleanup(isolation.stop)
    return environment
