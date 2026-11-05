# baton

When the user wants to know what baton can do for them, or to set it up, follow SETUP.md (you're inside a clone,
so use `./baton` where it says `~/.cache/baton-setup/baton`).

Working on baton itself: everything is the one file `baton` (Python 3.8+, standard library only). Tests run in
throwaway home folders: `python3 -m unittest discover -s tests`.
