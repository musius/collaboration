# coding=utf-8
from collections import namedtuple
import logging
import time

from libs.dmp.diff_match_patch import diff_match_patch
from misc import ApplicationSpecificAdapter


__author__ = 'snowy'

HistoryEntry = namedtuple('HistoryEntry', ['patch', 'timestamp', 'is_owner'])
logger = logging.getLogger(__name__)


class HistoryLine(object):
    def __init__(self, history_owner):
        """
        Линия истории патчей текущего application.
        :param history_owner: core.core.Application
        """
        self.owner = history_owner
        # История, которая держит патчи, обратные к тем, что лежат в self.history
        # необходимо для верного rollback патчей
        self.rollback_history = []
        # История примененных патчей
        self.history = []

    def clean(self):
        del self.history
        self.history = []

    def commit(self, patch, timestamp, is_owner):
        self._commit(HistoryEntry(patch, timestamp, is_owner), self.history)

    @staticmethod
    def _commit(entry, where):
        assert isinstance(entry, HistoryEntry)
        where.append(entry)

    def commit_with_rollback(self, forwards, backwards):
        """
        Комит изменений с rollback патчем
        :param forwards: HistoryEntry стандартный патч, применение которого ведет вперед по истории
        :param backwards: HistoryEntry патч, являющийся обратным к forwards
        """
        self._commit(forwards, self.history)
        self._commit(backwards, self.rollback_history)

    def get_all_since(self, timestamp):
        return [entry for entry in self.history if entry.timestamp > timestamp]


class RollbackFailedException(Exception):
    pass


class FailedToApplyPatchSilentlyException(Exception):
    pass


class TimeMachine(object):
    def __init__(self, history_line, owner):
        """

        :param history_line: HistoryLine
        :param owner: DiffMatchPatchAlgorithm
        """
        from core.core import DiffMatchPatchAlgorithm

        assert isinstance(owner, DiffMatchPatchAlgorithm)
        assert isinstance(history_line, HistoryLine)
        self.owner = owner
        self.history = history_line
        self.strict_dmp = diff_match_patch()
        self.strict_dmp.Match_Threshold = 0.0
        self.loose_dmp = diff_match_patch()
        self.loose_dmp.Match_Threshold = 1.0
        self.logger = ApplicationSpecificAdapter(logger, {'name': owner.name})
        # buffer text which determines state of the time machine
        self.model_text = None

    @staticmethod
    def get_current_timestamp():
        return time.time()

    def _pop_one_commit(self, pop_stack):
        to_be_rolled_back = self.history.rollback_history.pop()
        to_be_roll_forward = self.history.history.pop()
        pop_stack.append((to_be_rolled_back, to_be_roll_forward))
        return to_be_rolled_back, to_be_roll_forward

    def _rollback(self, to_be_rolled_back_patch):
        patchedText, result, commands = self.strict_dmp.patch_apply(to_be_rolled_back_patch, self.model_text)
        serialized = '\n'.join([str(patch) for patch in to_be_rolled_back_patch])
        if False in result:
            raise RollbackFailedException(
                'Check consistency of the rollback_history. '
                'Cannot rollback history. <patch>{0}</patch>'.format(serialized))
        self.logger.debug('rolled back: <patch>%s</patch>', serialized)
        self.logger.debug('rolled back commands: %s', commands)
        self.model_text = patchedText
        return commands

    # noinspection PyUnusedLocal
    def start_recovery(self, patch_objects, timestamp):
        """
        Процедура RECOVERY.
        :param patch_objects: list [libs.dmp.diff_match_patch.patch_obj] Список патчей
        :param timestamp: временная метка
        :return tuple of ([rollforward_command], [rollback_command]) which can be applied to sublime's view
        """
        assert self.owner.name != 'Coordinator'
        self.logger.info('starting recovery...')
        text_before = self.owner.currentText
        # to be recovered text:
        self.model_text = self.owner.currentText
        pop_stack = []
        rollback_commands = []
        d1d3 = None
        while True:  # todo: what if there is no pop and match?
            # pop one commit, roll it back and try patch
            to_be_rolled_back, _ = self._pop_one_commit(pop_stack)
            rollback_command = self._rollback(to_be_rolled_back.patch)
            rollback_commands.extend(rollback_command)
            # try patch
            perfectly_patched_text = self._try_patch(patch_objects)
            if perfectly_patched_text is not None:
                self.model_text = perfectly_patched_text
                d1d3 = self.model_text  # currentText = d1+d3
                self._rollforward(pop_stack)
                self.logger.info('recovery has stopped. Everything seems okay now. Lets try again')
                break
        patches = self.strict_dmp.patch_make(d1d3, self.model_text)
        patched_text, result, rollforward_commands = self.strict_dmp.patch_apply(patches, d1d3)
        return rollforward_commands, rollback_commands, d1d3  # d1 -> d1+d3(+)d2, d1+d2 -> d1

    def _try_patch(self, patch_objects):
        """
        Попробовать применить патч
        :param patch_objects:
        :return: bool is successful patch
        """
        patchedText, result, commands = self.strict_dmp.patch_apply(patch_objects, self.model_text)
        if False in result:
            return None
        else:
            # everything all right rolled back and patch is perfect match this version
            self.logger.debug('conflicts are fixed. The following patch\'s applied: <patch>%s</patch>',
                              ''.join([str(patch) for patch in patch_objects]))
            return patchedText

    def _rollforward(self, pop_stack):
        for _, forward in reversed(pop_stack):
            patchedText, result, commands = self.loose_dmp.patch_apply(forward.patch, self.model_text)
            serialized = '\n'.join([str(patch) for patch in forward.patch])
            if False in result:
                self.logger.debug('could not roll forward even with loose matching: <patch>%s</patch>', serialized)
            self.model_text = patchedText
            self.logger.debug('rolled forward: <patch>%s</patch>', serialized)