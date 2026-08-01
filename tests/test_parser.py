"""Decklist parsing: every format users actually paste."""
from mtgsim.cards import deck_names, load_deck, parse_decklist


def test_sections_and_setcodes():
    main, cmd = parse_decklist("""
Commander
1 Xyris, the Writhing Storm (c20)

Deck
4x Island (guru)
2 Lightning Bolt

Sideboard
1 Negate
""")
    assert cmd == "Xyris, the Writhing Storm"
    assert main.count("Island") == 4 and main.count("Lightning Bolt") == 2
    assert "Negate" not in main


def test_deckstats_play_sections():
    main, cmd = parse_decklist("""//deck-1
1 Sol Ring (c17)
10 Swamp (cma)

//play-1
1 Kambal, Consul of Allocation (kld)
""")
    assert cmd == "Kambal, Consul of Allocation"
    assert len(main) == 11


def test_cmdr_marker_and_comments():
    main, cmd = parse_decklist("""# my cool deck
1 Talrand, Sky Summoner *CMDR*
30 Island
""")
    assert cmd == "Talrand, Sky Summoner"
    assert main == ["Island"] * 30


def test_collector_number_suffix():
    main, _ = parse_decklist("1 Opt (eld) 59\n")
    assert main == ["Opt"]


def test_all_shipped_decks_validate(db):
    """Every deck in data/decks: parses, 99 cards, commander, all in DB."""
    for name in deck_names():
        main, cmd = load_deck(name, db)   # SystemExits loudly if invalid
        assert len(main) == 99, f"{name}: {len(main)}"
        assert cmd
