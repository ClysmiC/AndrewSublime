import sublime
import sublime_plugin

# --- Extra state stored per view to enable behavior similar to emacs 'trasient-mark-mode'

g_viewExDict = {}

class ViewEx():
	def __init__(self):
		self.mark = -1

def ensureViewEx(view):
	result = g_viewExDict.get(view.id())

	if result is None:
		result = ViewEx()
		g_viewExDict[view.id()] = result

	return result

def isMarkActive(viewEx, selection):
	return viewEx.mark != -1 and len(selection) == 1

def clearMark(viewEx):
	viewEx.mark = -1

def isSingleNonEmptySelection(selection):
	return len(selection) == 1 and selection[0].a != selection[0].b

def clearSelection(viewEx, selection, shouldClearMark=True):
	assert len(selection) > 0

	# NOTE - Clobbers existing selections and multi-cursor
	region = selection[0]
	selection.clear()
	selection.add(sublime.Region(region.b, region.b))

	if shouldClearMark:
		clearMark(viewEx)

def setMark(viewEx, selection, mark):
	assert mark >= 0

	clearSelection(viewEx, selection, shouldClearMark=False)
	viewEx.mark = mark

# --- Text Commands

class AlsTrySetMarkCommand(sublime_plugin.TextCommand):
	"""Sets the mark if there is an unambiguous cursor position (no selection, no multi-cursor)"""
	def run(self, edit):
		selection = self.view.sel()

		if len(selection) == 1:
			setMark(ensureViewEx(self.view), selection, selection[0].b)

class AlsClearSelectionOrCancel(sublime_plugin.TextCommand):
	"""Clears the selection (and the mark) if they are active. Otherwise, runs 'cancel'"""
	def run(self, edit):
		selection = self.view.sel()
		cleared = False

		if len(selection) == 1:
			region = selection[0]

			if not region.empty():
				clearSelection(ensureViewEx(self.view), selection)
				cleared = True

		if not cleared:
			self.view.run_command("cancel")

# --- Listeners

class AlsViewEventListener(sublime_plugin.ViewEventListener):
	def on_text_command(self, command_name, args):
		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		# NOTE - Altering the return value feeds the altered command back into this function.
		#  Only return a tuple if you have actually altered things!

		if command_name == "move":
			if isMarkActive(viewEx, selection) and not args.get("extend", False):
				print("extending move")
				args["extend"] = True
				return (command_name, args)

		return None


	def on_selection_modified(self): # NOTE - Runs any time the cursor moves (A single cursor is considered a 0-wide selection)
		pass
