"""
seed_themed_demo.py
--------------------
Adds 200+ realistic-feeling demo records to vaultgram.db: fan/community-style
accounts across cricket, football/FIFA, Bollywood, kabaddi, esports,
badminton and music, plus posts, likes, comments and follows so the feed
looks like an actual active app rather than placeholder rows.

Safe to re-run: usernames are checked against the DB and skipped if they
already exist, so running this twice won't create duplicates.

Usage:
    python seed_themed_demo.py
"""

import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "vaultgram.db"
DEMO_PASSWORD = "Demo@1234"

CITIES = ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar", "Bhavnagar",
          "Mumbai", "Delhi", "Bengaluru", "Pune", "Jaipur", "Chandigarh"]

ACCOUNTS = {
    "cricket": [
        ("CricketBuzzIndia", "Daily cricket updates, scores & memes\nNot affiliated with BCCI"),
        ("IPL_Frenzy", "Everything IPL - auctions, stats, banter\nDM your predictions"),
        ("BoundaryHuntersIN", "Six appeal since day one\nCricket > everything"),
        ("StumpsAndSwing", "Match commentary, our way\nFollow for live reactions"),
        ("GullyCricketClub", "From gully to galaxy\nTape-ball legends welcome"),
        ("DesiCricketTalk", "Stats, debates, hot takes\nNo bias, just passion"),
        ("OnDriveCricketHub", "Classic shots, classic vibes\nCricket purists corner"),
        ("TeamIndiaFanZone", "Bleed blue, live cricket\nJai Hind, Jai Cricket"),
        ("SixerSquadOfficial", "Big hits only\nFan page, unofficial"),
        ("CricCrazyGuj", "Gujarat's cricket pulse\nLocal grounds to world stage"),
    ],
    "football": [
        ("FIFA_ArenaIN", "FIFA / EA FC highlights & rage quits\nUlti squad builder"),
        ("FootballFeverIN", "Football never stops here\nLeagues, transfers, drama"),
        ("ChampionsLeagueBuzz", "UCL nights hit different\nFan page"),
        ("GoalMachineIndia", "Goals, skills, screamers\nTag your favourite strike"),
        ("UltrasIndiaFC", "Matchday energy, every day\nFootball is religion"),
        ("OffsideTrapTalk", "Tactics, banter, VAR rants\nUnofficial fan page"),
        ("TikiTakaTalkies", "Beautiful game, beautiful chaos\nPossession football fans"),
        ("WorldCupWatchIN", "World Cup forever loading\nFootball culture page"),
        ("ProClubsIndia", "EA FC Pro Clubs grind\nLooking for striker, DM"),
        ("FootballFridaysIN", "Weekend league recaps\nLocal turf to world stage"),
    ],
    "bollywood": [
        ("BollywoodBuzzIndia", "Trailers, gossip, box office\nUnofficial fan page"),
        ("FilmyGossipDaily", "Bollywood tea, served daily\nDM your scoops"),
        ("BoxOfficeBuzzIN", "Weekend numbers, hot takes\nNo spoilers, promise"),
        ("CinemaScreenIndia", "Reviews without the spoilers\nFilm lovers corner"),
        ("BollywoodDiariesIN", "Behind-the-scenes vibes\nFan-run page"),
        ("MovieMasalaIN", "Masala for your movie cravings\nTrailer reactions daily"),
        ("RedCarpetRewind", "Red carpet moments, recapped\nFashion + films"),
        ("OTTUpdatesIndia", "What to stream this week\nUnofficial recommendations"),
        ("TrailerTalkiesIN", "Trailer breakdowns & theories\nSpoiler-free zone"),
        ("DialogueBaaziIN", "Iconic Bollywood dialogues\nFan compilation page"),
    ],
    "kabaddi": [
        ("ProKabaddiFanIN", "Raid, tackle, repeat\nPKL season tracker"),
        ("RaidMastersIndia", "Kabaddi is underrated, fight us\nFan page"),
        ("KabaddiAddaIndia", "Desi sport, global heart\nMatch day updates"),
    ],
    "esports": [
        ("EsportsArenaIN", "BGMI, Valorant, FIFA - all of it\nScrims & highlights"),
        ("BGMI_SquadIN", "Chicken dinner chasers\nLFG, drop a comment"),
        ("ValorantIndiaHub", "Ace clips & rank grind\nDuo queue, DM open"),
        ("GamingZoneIndia", "All things gaming\nIndian esports updates"),
    ],
    "sports_general": [
        ("SmashCourtIndia", "Badminton rallies & rackets\nCourt-side updates"),
        ("OlympicSpiritIN", "Every four years, all year here\nSports for all"),
        ("ShuttleZoneIndia", "Net play to smashes\nBadminton fan community"),
        ("WrestleRingIndia", "Wrestling highlights & reactions\nUnofficial fan page"),
    ],
    "music": [
        ("IndiePopIndia", "New music, fresh sounds\nWeekly playlist drops"),
        ("BeatDropIndia", "Bass, beats, repeat\nMusic discovery page"),
        ("MusicMasalaIN", "Bollywood + indie + everything\nSong of the week"),
    ],
}

CAPTIONS = {
    "cricket": [
        "What a chase by Team India tonight! Cricket fever is real",
        "That last-over six gave me a heart attack",
        "Net practice before the big match, feeling good",
        "IPL auction day = pure chaos and excitement",
        "Stadium lights, packed stands, electric atmosphere",
        "Gully cricket beats any five-star gym session",
        "Spin vs pace - which do you fear more as a batter?",
        "Sunday league finals coming up, wish us luck",
        "This pitch is a bowler's dream today",
        "Catch of the season, hands down",
        "Six! Six! Six! This over is unreal",
        "Rain delay vibes, but the energy's still high",
    ],
    "football": [
        "Champions League nights hit different",
        "Ronaldo or Messi? Tag your pick below",
        "Just won my 10th match in a row on FIFA Ultimate Team",
        "Weekend turf football with the boys",
        "That last-minute goal though, unbelievable",
        "Transfer window rumours are getting wild this year",
        "VAR drama again, can we just play football",
        "New boots, new season, new me",
        "Five-a-side league starts tonight, let's go",
        "Free kick practice till the lights went out",
        "This derby atmosphere is unmatched",
        "Penalty shootouts are not for the weak-hearted",
    ],
    "bollywood": [
        "New trailer dropped and I'm not okay",
        "Weekend plan: movie marathon plus popcorn",
        "That dialogue gave me chills",
        "Box office numbers are insane this week",
        "First day first show energy is unmatched",
        "Soundtrack on loop since the trailer dropped",
        "This plot twist nobody saw coming",
        "Theatre to OTT in record time, loving it",
        "Award season predictions, who's your pick?",
        "Re-watching old classics this weekend",
        "That background score still gives goosebumps",
        "Costume design in this one is next level",
    ],
    "kabaddi": [
        "Raid of the season right there",
        "Tackle so clean it deserves a slow-mo replay",
        "PKL finals weekend, who's watching?",
        "Local kabaddi tournament was pure intensity today",
        "Underrated sport, unmatched passion",
    ],
    "esports": [
        "Chicken dinner after a wild final circle",
        "Clutched a 1v3 and I'm still shaking",
        "Scrims tonight, squad up if you're free",
        "New patch, new meta, relearning everything",
        "Rank grind till 2am, no regrets",
        "That ace clip is going straight to highlights",
    ],
    "sports_general": [
        "Smash, drop, repeat - badminton therapy session",
        "Olympic spirit doesn't need an Olympic year",
        "Court booked, rivalry on",
        "Wrestling highlights reel of the week",
        "Early morning rally practice, worth it",
    ],
    "music": [
        "New track on loop since this morning",
        "Found a hidden gem in the indie scene this week",
        "Concert night, voice gone, no regrets",
        "This bassline though",
        "Playlist update incoming, drop your suggestions",
    ],
}

COMMENT_POOL = [
    "Fire post!", "This is amazing!", "Goosebumps", "No words, just wow",
    "Took the words right out of my mouth", "Following for more of this",
    "100% agree", "Can't stop watching this", "This made my day",
    "Underrated post, more people need to see this", "Saving this",
    "Tag me next time", "Absolute scenes", "Chills", "LOVE this",
    "Who else is hyped??", "This deserves way more likes",
    "Best one yet", "Insane content as always", "Need a part 2 of this!",
]

LOCATION_TAGS = {
    "cricket": ["Stadium", "Practice Nets", "IPL Auction Hall", "Local Ground", "Cricket Academy"],
    "football": ["Turf Arena", "Stadium", "5-a-side Court", "Training Ground", "Sports Bar"],
    "bollywood": ["Mumbai Studios", "PVR Cinemas", "Premiere Night", "Film City", "Home Theatre"],
    "kabaddi": ["Kabaddi Arena", "PKL Stadium", "Local Akhada", "Sports Complex"],
    "esports": ["Gaming Lounge", "Home Setup", "LAN Event", "Esports Arena"],
    "sports_general": ["Badminton Court", "Sports Complex", "Stadium", "Community Centre"],
    "music": ["Studio", "Live Venue", "Concert Hall", "Home Studio"],
}


def random_timestamp(days_back=180):
    delta = timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    return (datetime.now() - delta).strftime("%Y-%m-%d %H:%M:%S")


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys = ON")

    existing_usernames = {r[0] for r in cur.execute("SELECT username FROM users")}

    pwd_hash = generate_password_hash(DEMO_PASSWORD, method="pbkdf2:sha256")
    new_usernames = []
    skipped = []

    flat_accounts = []
    for category, accounts in ACCOUNTS.items():
        for username, bio in accounts:
            flat_accounts.append((category, username, bio))

    for category, username, bio in flat_accounts:
        if username in existing_usernames:
            skipped.append(username)
            continue
        email = f"{username.lower()}@samvadx.demo"
        city = random.choice(CITIES)
        full_bio = f"{bio}\nLocation: {city}"
        cur.execute(
            "INSERT INTO users(username, email, password, profile_pic, bio, last_seen, created_at) "
            "VALUES (?, ?, ?, 'default.png', ?, ?, ?)",
            (username, email, pwd_hash, full_bio, random_timestamp(30), random_timestamp(365)),
        )
        existing_usernames.add(username)
        new_usernames.append((username, category))

    con.commit()
    print(f"Created {len(new_usernames)} themed users ({len(skipped)} already existed, skipped).")

    all_usernames = [r[0] for r in cur.execute("SELECT username FROM users")]

    posts_dir = BASE_DIR / "static" / "posts"
    image_files = sorted(
        [f.name for f in posts_dir.glob("post*.jpg")],
        key=lambda n: int("".join(filter(str.isdigit, n)) or 0),
    )
    if not image_files:
        image_files = ["default.png"]

    new_post_ids = []
    for username, category in new_usernames:
        for _ in range(2):
            caption = random.choice(CAPTIONS[category])
            location = random.choice(LOCATION_TAGS[category])
            image = random.choice(image_files)
            created = random_timestamp(150)
            cur.execute(
                "INSERT INTO posts(username, image, caption, location, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, image, caption, location, created),
            )
            new_post_ids.append((cur.lastrowid, category))

    con.commit()
    print(f"Created {len(new_post_ids)} themed posts.")

    follow_count = 0
    for username, _ in new_usernames:
        others = [u for u in all_usernames if u != username]
        n = random.randint(6, 15)
        for followee in random.sample(others, min(n, len(others))):
            try:
                cur.execute(
                    "INSERT INTO follows(follower, followee, status, created_at) VALUES (?, ?, 'accepted', ?)",
                    (username, followee, random_timestamp(200)),
                )
                follow_count += 1
            except sqlite3.IntegrityError:
                pass
    con.commit()
    print(f"Created {follow_count} follow relationships.")

    like_count = 0
    comment_count = 0
    for post_id, category in new_post_ids:
        likers = random.sample(all_usernames, min(random.randint(5, 35), len(all_usernames)))
        for liker in likers:
            try:
                cur.execute(
                    "INSERT INTO likes(post_id, username, created_at) VALUES (?, ?, ?)",
                    (post_id, liker, random_timestamp(140)),
                )
                like_count += 1
            except sqlite3.IntegrityError:
                pass

        n_comments = random.randint(0, 6)
        commenters = random.sample(all_usernames, min(n_comments, len(all_usernames)))
        for commenter in commenters:
            cur.execute(
                "INSERT INTO comments(post_id, username, text, created_at) VALUES (?, ?, ?, ?)",
                (post_id, commenter, random.choice(COMMENT_POOL), random_timestamp(130)),
            )
            comment_count += 1

    con.commit()
    print(f"Created {like_count} likes and {comment_count} comments.")

    notif_count = 0
    cutoff = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        "SELECT post_id, username, created_at FROM likes WHERE created_at >= ? ORDER BY created_at DESC LIMIT 150",
        (cutoff,),
    )
    for post_id, liker, created in cur.fetchall():
        row = con.execute("SELECT username FROM posts WHERE id=?", (post_id,)).fetchone()
        if row and row[0] != liker:
            con.execute(
                "INSERT INTO notifications(username, actor, type, post_id, created_at) VALUES (?, ?, 'like', ?, ?)",
                (row[0], liker, post_id, created),
            )
            notif_count += 1

    cur.execute(
        "SELECT follower, followee, created_at FROM follows WHERE created_at >= ? ORDER BY created_at DESC LIMIT 100",
        (cutoff,),
    )
    for follower, followee, created in cur.fetchall():
        con.execute(
            "INSERT INTO notifications(username, actor, type, created_at) VALUES (?, ?, 'follow', ?)",
            (followee, follower, created),
        )
        notif_count += 1

    con.commit()
    print(f"Created {notif_count} notifications.")

    total_new_rows = len(new_usernames) + len(new_post_ids) + follow_count + like_count + comment_count + notif_count
    print(f"\nTotal new rows added: {total_new_rows}")
    print(f"All seeded accounts use the password: {DEMO_PASSWORD}")

    con.close()


if __name__ == "__main__":
    main()
