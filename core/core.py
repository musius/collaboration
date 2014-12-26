# coding=utf-8
from twisted.protocols.amp import Command, Unicode, Boolean, CommandLocator

from libs.dmp import diff_match_patch


__author__ = 'snowy'


class Patch(Unicode):
    pass


class NoTextAvailableException(Exception):
    pass


def PatchIsNotApplicableException(Exception):
    pass


class GetTextCommand(Command):
    response = [('text', Unicode())]
    errors = {NoTextAvailableException: 'Невозможно получить текст'}


class ApplyPatchCommand(Command):
    arguments = [('patch', Patch())]
    response = [('succeed', Boolean())]
    errors = {PatchIsNotApplicableException: 'Патч не может быть применен'}


class DiffMatchPatchAlgorithm(CommandLocator):
    clientProtocol = None

    def __init__(self, initialText):
        self.currentText = initialText
        self.dmp = diff_match_patch()

    @property
    def text(self):
        return self.currentText

    def setText(self, text):
        """
        Заменить текущий текст без сайд-эффектов
        :param text: str
        """
        self.currentText = text

    def onTextChanged(self, nextText):
        """
        Установить текст, посчитать дельту, отправить всем участникам сети патч
        :param nextText: текст, который является более новой версией текущего текст self.currentText
        """
        patches = self.dmp.patch_make(self.currentText, nextText)
        serialized = self.dmp.patch_toText(patches)
        return self.clientProtocol.callRemote(ApplyPatchCommand, patch=serialized)

    @ApplyPatchCommand.responder
    def applyRemotePatch(self, patch):
        _patch = self.dmp.patch_fromText(patch)
        patchedText, result = self.dmp.patch_apply(_patch, self.currentText)
        if False in result:
            return {'succeed': False}
        self.currentText = patchedText
        return {'succeed': True}

    @GetTextCommand.responder
    def getTextRemote(self):
        if self.text is None:
            raise NoTextAvailableException()
        return {'text': self.text}