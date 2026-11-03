# Usage

- Swipe left or right: next or previous card (over 60px, more than twice the
  vertical move, within 0.6s)
- Tap: click on a browser or screen card. Vertical drag: scroll it
- Tap the title: reload the card list
- `+`: new terminal, new shell, new browser (asks for a URL). `×`: close the
  current card (asks first)
- Shell cards run a shell inside swiv itself, with no window on the desktop.
  Their input also works while the desktop is locked; the shell keeps running
  while the phone is away and ends when the card is closed
- Input box: sends text + Enter. Key buttons: Esc, Tab, Shift+Tab, arrows, ^C,
  Backspace, Enter
- File card: tap a folder to enter it. ↑ goes back to the list while viewing a
  document, otherwise to the parent folder

Typing into a terminal card brings that terminal window to the front on the
desktop and takes the focus. Closing a terminal with `×` also ends the program
running in it.

Screen cards (Windows, macOS) show the whole monitor shrunk to the phone width,
so text is small. A tap moves the real mouse there and clicks, the input box and
key buttons type on the real keyboard. A screen card cannot be closed.

Back to the [README](../README.md).
