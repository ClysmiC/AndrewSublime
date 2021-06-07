import sublime
import sublime_plugin

# --- Extra state stored per view to enable behavior similar to emacs 'trasient-mark-mode'

g_viewExDict = {}

class ViewEx():
	def __init__(self):
		self.mark = -1
		self.cntModificationToIgnore = 0

def ensureViewEx(view):
	result = g_viewExDict.get(view.id())

	if result is None:
		result = ViewEx()
		g_viewExDict[view.id()] = result

	return result

def isMarkActive(viewEx, selection):
	return viewEx.mark != -1 and len(selection) == 1

def clearMark(viewEx):
	if False: # debug
		print("CLR")
	viewEx.mark = -1

def isSingleNonEmptySelection(selection):
	return len(selection) == 1 and selection[0].a != selection[0].b

def clearSelectionToSingleCursor(viewEx, selection, shouldClearMark=True):
	assert len(selection) > 0

	# NOTE - Clobbers existing selections and multi-cursor
	region = selection[0]
	selection.clear()
	selection.add(sublime.Region(region.b, region.b))

	if shouldClearMark:
		clearMark(viewEx)

def setMark(viewEx, selection, mark):
	assert mark >= 0

	clearSelectionToSingleCursor(viewEx, selection, shouldClearMark=False)
	viewEx.mark = mark

# --- Text Commands

class AlsTrySetMarkCommand(sublime_plugin.TextCommand):
	"""Sets the mark if there is an unambiguous cursor position (no selection, no multi-cursor)"""
	def run(self, edit):
		selection = self.view.sel()

		if len(selection) == 1:
			setMark(ensureViewEx(self.view), selection, selection[0].b)



class AlsClearSelectionCommand(sublime_plugin.TextCommand):
	"""Clears the selection (and the mark) if they are active."""
	def run(self, edit):
		clearSelectionToSingleCursor(ensureViewEx(self.view), self.view.sel())

class AlsTestInputHandlerCommand(sublime_plugin.WindowCommand):
	def run(self):
		pass

	def input(self, arg):
		# return AlsTextInputHandler()
		pass

# --- Listeners

class AlsViewEventListener(sublime_plugin.ViewEventListener):
	def on_text_command(self, command_name, args):
		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		# NOTE - Altering the return value feeds the altered command back into this function.
		#  Only return a tuple if you have actually altered things, otherwise there is inifnite recursion!

		if command_name == "move" or command_name == "move_to":
			if isMarkActive(viewEx, selection) and not args.get("extend", False):
				args["extend"] = True
				return (command_name, args)

		# NOTE - on_modified doesn't know what command caused the modification,
		#  We squirrel that info away so on_modified knows if it should drop the selection

		elif command_name == "swap_line_up" or \
			 command_name == "swap_line_down" or \
			 command_name == "indent" or \
			 command_name == "unindent":
			viewEx.cntModificationToIgnore += 1

		# Execute command unmodified

		return None

	def on_post_text_command(self, command_name, args):
		# NOTE - Commands that don't modify the buffer are handled here.
		#  All buffer-modifying commands are handled in on_modified

		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		if command_name == "copy":
			clearSelectionToSingleCursor(ensureViewEx(self.view), self.view.sel())

	# def on_window_command(self, window, command_name, args):
		# pass

	def on_modified(self):
		# NOTE - Anything that affects contents of buffer will hook into
		#  this function

		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		if viewEx.cntModificationToIgnore > 0:
			viewEx.cntModificationToIgnore -= 1

		elif isMarkActive(viewEx, selection):
			clearSelectionToSingleCursor(ensureViewEx(self.view), self.view.sel())

	def on_selection_modified(self):
		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		# NOTE - Stretch the selection to accomodate incremental search, etc.

		if isMarkActive(viewEx, selection):
			region = selection[0]

			if viewEx.mark < region.begin():
				selection.clear()
				selection.add(sublime.Region(viewEx.mark, region.end()))

			elif viewEx.mark > region.end():
				selection.clear()
				selection.add(sublime.Region(viewEx.mark, region.begin()))

class AlsEventListener(sublime_plugin.EventListener):
	def on_text_command(self, view, command_name, args):
		# print("e/t " + command_name)
		pass

	def on_window_command(self, window, command_name, args):
		# print("e/w " + command_name)
		pass

# class AlsTextInputHandler(sublime_plugin.TextInputHandler):
# 	def name(self):
# 		return "this string is the name of the argument that is being passed to the command"

# 	def placeholder(self):
# 		return "Big Chungus"

# 	def confirm(self, text):
# 		print(text)

