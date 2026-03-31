import importlib
import sys
import tempfile

import pytest


@pytest.fixture()
def client(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="darts-test-") as db_dir:
        db_path = f"{db_dir}/test.db"

        monkeypatch.setenv("FLASK_ENV", "testing")
        monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_path}")

        sys.modules.pop("app", None)
        app_module = importlib.import_module("app")
        app, db = app_module.app, app_module.db

        app.config.update(
            {
                "TESTING": True,
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            }
        )

        with app.app_context():
            db.drop_all()
            db.create_all()

        with app.test_client() as test_client:
            yield test_client


def add_player(client, name):
    res = client.post("/api/players", json={"name": name})
    assert res.status_code == 201
    return res.get_json()["id"]


def test_turn_scoring_divisible_by_five(client):
    alice = add_player(client, "Alice")
    game = client.post("/api/games", json={"ordered_player_ids": [alice]}).get_json()["game"]

    res = client.post(
        f"/api/games/{game['id']}/turn",
        json={"player_id": alice, "total_points": 20},
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["turn"]["counted"] is True
    assert payload["turn"]["fives_awarded"] == 4


def test_turn_scoring_not_divisible_by_five(client):
    alice = add_player(client, "Alice")
    game = client.post("/api/games", json={"ordered_player_ids": [alice]}).get_json()["game"]

    res = client.post(
        f"/api/games/{game['id']}/turn",
        json={"player_id": alice, "total_points": 3},
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["turn"]["counted"] is False
    assert payload["turn"]["fives_awarded"] == 0


def test_first_to_55_fives_wins(client):
    alice = add_player(client, "Alice")
    bob = add_player(client, "Bob")
    game = client.post("/api/games", json={"ordered_player_ids": [alice, bob]}).get_json()["game"]

    # 13 rounds of 20 pts (4 fives each) -> 52 fives for Alice
    for _ in range(13):
        a_turn = client.post(
            f"/api/games/{game['id']}/turn",
            json={"player_id": alice, "total_points": 20},
        )
        assert a_turn.status_code == 200

        b_turn = client.post(
            f"/api/games/{game['id']}/turn",
            json={"player_id": bob, "total_points": 0},
        )
        assert b_turn.status_code == 200

    # 15 pts (3 fives) -> 52 + 3 = 55 exactly -> win
    final = client.post(
        f"/api/games/{game['id']}/turn",
        json={"player_id": alice, "total_points": 15},
    )
    assert final.status_code == 200
    final_payload = final.get_json()

    assert final_payload["game"]["status"] == "finished"
    assert final_payload["game"]["winner_player_id"] == alice


def test_bust_when_exceeding_55(client):
    alice = add_player(client, "Alice")
    game = client.post("/api/games", json={"ordered_player_ids": [alice]}).get_json()["game"]

    # 13 turns of 20 (52 fives) + 1 turn of 5 (1 five) = 53 fives
    for _ in range(13):
        client.post(f"/api/games/{game['id']}/turn", json={"player_id": alice, "total_points": 20})
    client.post(f"/api/games/{game['id']}/turn", json={"player_id": alice, "total_points": 5})

    # Score is now 53; scoring 25 (5 fives) would push to 58 -> bust
    bust = client.post(
        f"/api/games/{game['id']}/turn",
        json={"player_id": alice, "total_points": 25},
    )
    assert bust.status_code == 200
    payload = bust.get_json()
    assert payload["turn"]["counted"] is False
    assert payload["turn"]["fives_awarded"] == 0
    assert payload["game"]["status"] == "active"
    alice_score = next(p for p in payload["game"]["players"] if p["id"] == alice)
    assert alice_score["fives"] == 53


def test_game_history_persists(client):
    p1 = add_player(client, "P1")
    game = client.post("/api/games", json={"ordered_player_ids": [p1]}).get_json()["game"]

    # 11 turns of 25 (5 fives each) = 55 fives exactly -> game finishes
    for _ in range(11):
        client.post(f"/api/games/{game['id']}/turn", json={"player_id": p1, "total_points": 25})

    history = client.get("/api/games/history?limit=10")
    assert history.status_code == 200
    games = history.get_json()
    assert len(games) == 1
    assert games[0]["winner_player_id"] == p1


def test_quit_active_game(client):
    p1 = add_player(client, "Quitter")
    game = client.post("/api/games", json={"ordered_player_ids": [p1]}).get_json()["game"]

    quit_res = client.delete(f"/api/games/{game['id']}")
    assert quit_res.status_code == 200
    assert quit_res.get_json()["ok"] is True

    active = client.get("/api/games/active")
    assert active.status_code == 200
    assert active.get_json()["game"] is None

    # Starting another game should now be allowed.
    next_game = client.post("/api/games", json={"ordered_player_ids": [p1]})
    assert next_game.status_code == 201
