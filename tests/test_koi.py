"""星盘玩法（KOI）测试：规则、服务、指令、八字与八卦。"""

import random
import tempfile
import time
import unittest
from pathlib import Path

from src.config import BotConfig
from src.db.schema import initialize_db
from src.koi import rules
from src.koi.handler import KoiHandler, looks_like_koi_command
from src.koi.service import KoiService
from src.koi.signs import SIGNS, all_sign_ids, signs_by_level
from src.koi.store import KoiStore
from src.game_utils import local_day, local_week


def make_msg(content="求签", *, sender_id="wxid_a", name="张三",
             chat_id="1@chatroom", message_id=None, msg_type=1):
    return {
        "message_id": message_id or f"m-{time.time_ns()}",
        "chat_id": chat_id,
        "group_name": "测试群",
        "sender_id": sender_id,
        "sender_name": name,
        "content": content,
        "msg_type": msg_type,
        "timestamp": int(time.time()),
    }


class KoiTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.config = BotConfig()
        self.config.koi_enabled = True
        self.config.koi_groups = ["*"]
        self.store = KoiStore(self.conn)
        self.service = KoiService(
            self.store, self.config, rng=random.Random(7))
        self.handler = KoiHandler(self.service)
        self.msg = make_msg()

    def tearDown(self):
        self.conn.close()


class RulesTests(unittest.TestCase):
    def test_sign_catalog_matches_levels(self):
        self.assertEqual(len(SIGNS), 36)
        self.assertEqual(len(set(all_sign_ids())), 36)
        expected = {
            "上上签": 1, "大吉": 2, "中吉": 6,
            "小吉": 15, "末吉": 9, "凶": 3,
        }
        for level, count in expected.items():
            self.assertEqual(len(signs_by_level(level)), count)

    def test_level_distribution(self):
        rng = random.Random(1)
        counts = {}
        for _ in range(2000):
            level = rules.roll_level(rng)["name"]
            counts[level] = counts.get(level, 0) + 1
        self.assertAlmostEqual(counts.get("小吉", 0) / 2000, 0.40, delta=0.04)
        self.assertAlmostEqual(counts.get("中吉", 0) / 2000, 0.18, delta=0.04)

    def test_rank_progression(self):
        self.assertEqual(rules.rank_for(0)["name"], "咸鱼")
        self.assertEqual(rules.rank_for(15)["name"], "小虾")
        self.assertEqual(rules.rank_for(299)["name"], "鲤鱼")
        self.assertEqual(rules.rank_for(3000)["name"], "锦鲤王")
        self.assertEqual(rules.rank_for(15)["to_next"], 35)

    def test_streak_bonus_caps(self):
        self.assertEqual(rules.streak_bonus(0), 0)
        self.assertEqual(rules.streak_bonus(3), 6)
        self.assertEqual(rules.streak_bonus(99), 20)

    def test_sign_from_date(self):
        self.assertEqual(rules.sign_from_date(10, 24), "天蝎座")
        self.assertEqual(rules.sign_from_date(11, 22), "天蝎座")
        self.assertEqual(rules.sign_from_date(11, 23), "射手座")
        self.assertEqual(rules.sign_from_date(1, 19), "摩羯座")
        self.assertEqual(rules.sign_from_date(1, 20), "水瓶座")
        self.assertIsNone(rules.sign_from_date(13, 1))

    def test_event_and_lucky_sign_are_stable(self):
        self.assertEqual(
            rules.event_for("1@chatroom", "2026-11-17"),
            rules.event_for("1@chatroom", "2026-11-17"),
        )
        self.assertEqual(
            rules.lucky_sign("1@chatroom", "2026-W47"),
            rules.lucky_sign("1@chatroom", "2026-W47"),
        )

    def test_normalizers(self):
        self.assertEqual(rules.normalize_sign("天蝎"), "天蝎座")
        self.assertEqual(rules.normalize_sign("天蝎座"), "天蝎座")
        self.assertEqual(rules.normalize_mbti("infp"), "INFP")
        self.assertIsNone(rules.normalize_mbti("ABCD"))


class ServiceTests(KoiTestBase):
    def test_first_sign_grants_welcome_and_merit(self):
        reply = self.service.sign(self.msg)
        self.assertIn("求得", reply)
        self.assertEqual(
            self.store.balance("1@chatroom", "wxid_a"), 150)
        self.assertEqual(self.store.sign_count("1@chatroom", "wxid_a"), 1)

    def test_sign_is_once_per_day(self):
        self.service.sign(self.msg)
        reply = self.service.sign(self.msg)
        self.assertIn("今天已经求过签", reply)
        self.assertEqual(
            self.store.balance("1@chatroom", "wxid_a"), 150)

    def test_streak_increments_from_yesterday(self):
        day = local_day()
        yesterday = rules.today_minus_days(day)
        self.store.upsert_daily({
            "chat_id": "1@chatroom", "sender_id": "wxid_a",
            "day": yesterday, "sign_id": 1, "sign_level": "上上签",
            "luck": 90, "yi": "观星", "streak_days": 4,
            "rerolls": 0, "release_used": 0, "draws_used": 0,
            "chat_merit": 0, "pool_in": 0, "observed": 0,
        })
        self.service.sign(self.msg)
        daily = self.store.get_daily("1@chatroom", "wxid_a", day)
        self.assertEqual(daily["streak_days"], 5)

    def test_observe_grants_merit_once(self):
        day = local_day()
        event = rules.event_for("1@chatroom", day)
        before = self.store.balance("1@chatroom", "wxid_a")
        self.service.observe(self.msg)
        after = self.store.balance("1@chatroom", "wxid_a")
        expected = 100 + 30 + int(event.get("observe_bonus", 0) or 0)
        if self.store.get_daily("1@chatroom", "wxid_a", day).get("yi") == \
                "观星":
            expected += 10
        self.assertEqual(after, expected)
        self.assertGreater(after, before)
        self.assertIn("今天已经观过星", self.service.observe(self.msg))

    def test_release_costs_merit_and_adds_fortune(self):
        self.service.sign(self.msg)
        reply = self.service.release(self.msg)
        self.assertIn("放生完成", reply)
        self.assertGreater(
            self.store.fortune("1@chatroom", "wxid_a"), 0)
        self.assertIn("今天已经放生", self.service.release(self.msg))

    def test_release_rejects_insufficient_merit(self):
        self.config.koi_welcome_merit = 0
        reply = self.service.release(self.msg)
        self.assertIn("香火不足", reply)
        self.assertEqual(
            self.store.fortune("1@chatroom", "wxid_a"), 0)

    def test_reroll_is_once_per_day(self):
        self.service.sign(self.msg)
        reply = self.service.reroll(self.msg)
        self.assertIn("新签", reply)
        daily = self.store.get_daily(
            "1@chatroom", "wxid_a", local_day())
        self.assertEqual(daily["rerolls"], 1)
        self.assertIn("今天已经转运", self.service.reroll(self.msg))

    def test_card_and_book(self):
        self.service.sign(self.msg)
        card = self.service.card(self.msg)
        self.assertIn("【功德卡】", card)
        self.assertIn("气运", card)
        book = self.service.sign_book(self.msg)
        self.assertIn("【签谱】", book)
        self.assertIn("1/36", book)

    def test_leaderboard(self):
        self.service.sign(self.msg)
        self.service.release(self.msg)
        board = self.service.leaderboard(self.msg)
        self.assertIn("【气运榜】", board)
        self.assertIn("张三", board)

    def test_identity_setup(self):
        reply = self.service.set_sign(self.msg, "设置生日 10-24")
        self.assertIn("天蝎座", reply)
        profile = self.store.get_profile("1@chatroom", "wxid_a")
        self.assertEqual(profile["sign"], "天蝎座")
        self.assertTrue(profile["moon_sign"])
        self.assertTrue(profile["rising_sign"])

        reply = self.service.set_mbti(self.msg, "我是 INFP")
        self.assertIn("INFP", reply)
        self.assertEqual(
            self.store.get_profile("1@chatroom", "wxid_a")["mbti"],
            "INFP",
        )

    def test_chat_merit_is_capped_and_idempotent(self):
        day = local_day()
        limit = self.config.koi_chat_merit_daily_cap
        rewarded = 0
        for i in range(limit + 5):
            rewarded += self.service.award_chat_merit(
                make_msg("聊天", message_id=f"chat-{i}"))
        self.assertEqual(rewarded, limit)
        daily = self.store.get_daily("1@chatroom", "wxid_a", day)
        self.assertEqual(daily["chat_merit"], limit)
        # Same message id twice does not double-award.
        self.assertEqual(
            self.service.award_chat_merit(
                make_msg("聊天", sender_id="wxid_dup",
                         name="王五", message_id="dup")),
            1,
        )
        self.assertEqual(
            self.service.award_chat_merit(
                make_msg("聊天", sender_id="wxid_dup",
                         name="王五", message_id="dup")),
            0,
        )

    def test_old_and_new_member_leaderboard_names(self):
        li = make_msg(sender_id="wxid_b", name="李四")
        self.service.sign(li)
        self.service.release(li)
        board = self.service.leaderboard(
            make_msg(sender_id="wxid_a", name="张三"))
        self.assertIn("李四", board)


class HandlerTests(KoiTestBase):
    def test_command_detection(self):
        self.assertTrue(looks_like_koi_command("求签"))
        self.assertTrue(looks_like_koi_command("我是 天蝎座"))
        self.assertTrue(looks_like_koi_command("我是 INFP"))
        self.assertFalse(looks_like_koi_command("我是谁"))
        self.assertFalse(looks_like_koi_command("总结一下"))

    def test_handler_labels_sender(self):
        reply = self.handler.handle("求签", self.msg)
        self.assertTrue(reply.startswith("张三："))
        self.assertIn("求得", reply)

    def test_new_commands_are_implemented(self):
        reply = self.handler.handle("开光", self.msg)
        self.assertIn("开光得", reply)

        reply = self.handler.handle("周运", self.msg)
        self.assertIn("本周运势", reply)

        reply = self.handler.handle("点星 青龙", self.msg)
        self.assertIn("点青龙第 1 星", reply)

    def test_help_shows_only_star_game(self):
        from src.admin import AdminCommandHandler
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        cfg = SimpleNamespace(
            koi_enabled=True, todo_enabled=True,
            summarize_enabled=True,
        )
        handler = AdminCommandHandler(MagicMock())
        reply = handler.handle("帮助", "张三", config=cfg,
                               is_admin=False)
        self.assertIn("【星盘】", reply)
        self.assertIn("求签", reply)
        self.assertNotIn("【装备】", reply)


class RouterKoiTests(unittest.TestCase):
    """End-to-end wiring: koi command -> router -> reply."""

    def setUp(self):
        from unittest.mock import MagicMock
        from src.admin import AdminCommandHandler
        from src.db.store import MessageStore
        from src.nickname import NicknameService
        from src.router import MessageRouter
        from src.trigger.detector import TriggerDetector

        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.config = BotConfig()
        self.config.koi_enabled = True
        self.config.koi_groups = ["*"]
        self.config.todo_enabled = False
        self.config.sticky_mention_enabled = False
        self.config.ai_enabled = False
        self.config.admin_wxid = "wxid_admin"
        nicks = NicknameService(
            path=Path(tempfile.mkdtemp()) / "nicknames.json")
        self.router = MessageRouter(
            store=self.messages,
            detector=TriggerDetector(
                keywords=[], bot_display_name="群聊小助手"),
            summarizer=MagicMock(),
            admin_handler=AdminCommandHandler(nicks),
            nickname_service=nicks,
            config=self.config,
        )
        self._seq = 0

    def tearDown(self):
        self.conn.close()

    def _message(self, content, sender_id="wxid_a", name="张三"):
        self._seq += 1
        return {
            "message_id": f"koi-router-{self._seq}",
            "chat_id": "123@chatroom",
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": name,
            "content": content,
            "msg_type": 1,
            "timestamp": int(time.time()),
            "is_at_mentioned": False,
        }

    def test_star_game_is_wired(self):
        self.assertIsNotNone(self.router._koi_handler)
        reply = self.router.handle(self._message("求签"))
        self.assertIn("求得", reply)

    def test_help_only_lists_star_game(self):
        reply = self.router.handle(self._message("帮助"))
        self.assertIn("【星盘】", reply)
        self.assertIn("求签", reply)

    def test_chat_merit_runs_through_router(self):
        self.router.handle(self._message("大家好"))
        balance = self.router._koi_service._store.balance(
            "123@chatroom", "wxid_a")
        # 100 welcome + 1 chat merit.
        self.assertEqual(balance, 101)


class FeatureTests(KoiTestBase):
    """P1/P2 systems: 开光、星盘、锦鲤池、周运、社交。"""

    def feed(self, reason, n, *, sender_id="wxid_a", name="张三",
             amount=0, kind="merit"):
        for i in range(int(n)):
            self.store.grant(
                "1@chatroom", sender_id, name, kind, amount, reason,
                ref_id=f"test-{reason}-{sender_id}-{i}-{time.time_ns()}")

    def test_open_light_limit_and_pity(self):
        self.store.grant("1@chatroom", "wxid_a", "张三", "merit",
                         500, "test", ref_id="light-bank")
        for _ in range(3):
            reply = self.service.open_light(self.msg)
            self.assertIn("开光得", reply)
        self.assertIn("已经开光 3 次", self.service.open_light(self.msg))

        other = make_msg(sender_id="wxid_b", name="李四")
        self.store.pity_set("1@chatroom", "wxid_b", 29)
        reply = self.service.open_light(other)
        self.assertIn("世界", reply)
        self.assertEqual(
            self.store.pity_get("1@chatroom", "wxid_b"), 0)
        self.assertGreaterEqual(
            self.store.collection_count("1@chatroom", "wxid_b", "tarot"), 1)

    def test_point_star_requirements_and_cost(self):
        self.store.grant("1@chatroom", "wxid_a", "张三", "merit",
                         1000, "test", ref_id="star-bank")
        self.store.grant("1@chatroom", "wxid_a", "张三", "fortune",
                         100, "test", ref_id="star-fortune")
        self.feed("sign", 3)
        before = self.store.balance("1@chatroom", "wxid_a")
        reply = self.service.point_star(self.msg, "青龙")
        self.assertIn("点亮青龙第 1 星", reply)
        self.assertEqual(
            self.store.star_counts("1@chatroom", "wxid_a")["qinglong"], 1)
        self.assertLess(self.store.balance("1@chatroom", "wxid_a"), before)
        reply = self.service.point_star(self.msg, "青龙")
        self.assertIn("还差", reply)

    def test_benming_star_first_node_half_cost(self):
        self.service.set_sign(self.msg, "我是 天蝎座")
        self.feed("release", 1)
        self.store.grant("1@chatroom", "wxid_a", "张三", "fortune",
                         5, "test", ref_id="benming-fortune")
        self.store.grant("1@chatroom", "wxid_a", "张三", "merit",
                         1000, "test", ref_id="benming-bank")
        before = self.store.balance("1@chatroom", "wxid_a")
        reply = self.service.point_star(self.msg, "玄武")
        self.assertIn("本命星效果翻倍", reply)
        self.assertEqual(
            before - self.store.balance("1@chatroom", "wxid_a"), 40)

    def test_mbti_free_first_star(self):
        reply = self.service.set_mbti(
            make_msg(sender_id="wxid_c", name="王五"), "我是 INFP")
        self.assertIn("免费点亮", reply)
        counts = self.store.star_counts("1@chatroom", "wxid_c")
        self.assertEqual(counts["xuanwu"], 1)

    def test_star_set_bonus_and_awakening(self):
        for key in ("qinglong", "baihu", "zhuque", "xuanwu"):
            for index in range(1, 8):
                self.store.add_star(
                    "1@chatroom", "wxid_a", key, index)
        bonuses = self.service._star_bonuses(
            "1@chatroom", "wxid_a")
        self.assertGreater(bonuses["merit_mult"], 1.15)
        self.assertGreater(bonuses["pool_weight"], 1.05)
        reply = self.service.awaken(self.msg, "青龙")
        self.assertIn("命格觉醒", reply)
        self.assertEqual(
            self.store.get_profile(
                "1@chatroom", "wxid_a")["awakening"], "qinglong")
        after = self.service._star_bonuses("1@chatroom", "wxid_a")
        self.assertGreater(after["merit_mult"], 1.3)

    def test_pool_two_participants(self):
        self.config.koi_pool_hour = 0
        day = local_day()
        self.store.grant("1@chatroom", "wxid_a", "张三", "merit",
                         1000, "test", ref_id="pool-a")
        self.store.grant("1@chatroom", "wxid_b", "李四", "merit",
                         1000, "test", ref_id="pool-b")
        self.store.pool_add(
            "1@chatroom", day, "wxid_a", "张三", 100, 50)
        self.store.pool_add(
            "1@chatroom", day, "wxid_b", "李四", 100, 50)
        announcement = self.service.settle_pool("1@chatroom", day)
        self.assertIn("中奖者", announcement)
        pool = self.store.pool_get("1@chatroom", day)
        self.assertEqual(pool["status"], "drawn")
        self.assertEqual(pool["total"], 200)
        self.assertGreater(
            int(pool["payout"]), 0)

    def test_pool_single_participant_refunds(self):
        self.config.koi_pool_hour = 0
        self.config.koi_welcome_merit = 0
        day = local_day()
        self.store.grant("1@chatroom", "wxid_a", "张三", "merit",
                         500, "test", ref_id="pool-solo")
        reply = self.service.pool_bet(self.msg, "100")
        self.assertIn("已投入", reply)
        self.service.settle_pool("1@chatroom", day)
        self.assertEqual(
            self.store.pool_get("1@chatroom", day)["status"], "refunded")
        self.assertEqual(
            self.store.balance("1@chatroom", "wxid_a"), 500)

    def test_duel_star(self):
        self.store.add_star("1@chatroom", "wxid_a", "baihu", 1)
        self.store.add_star("1@chatroom", "wxid_b", "baihu", 1)
        msg = make_msg(sender_id="wxid_a", name="张三")
        msg["mentioned_ids"] = ["wxid_b"]
        for _ in range(3):
            reply = self.service.duel_star(
                msg, "wxid_b", "李四")
            self.assertIn("斗星", reply)
        self.assertIn("已经用完", self.service.duel_star(
            msg, "wxid_b", "李四"))

    def test_synastry_adds_luck(self):
        self.service.set_sign(self.msg, "我是 天蝎座")
        other = make_msg(sender_id="wxid_b", name="李四")
        self.service.set_sign(other, "我是 双鱼座")
        reply = self.service.synastry(
            self.msg, "wxid_b", "李四")
        self.assertIn("契合度", reply)
        daily = self.store.get_daily(
            "1@chatroom", "wxid_a", local_day())
        self.assertEqual(daily["extra_luck"], 1)
        self.assertIn("已经合过", self.service.synastry(
            self.msg, "wxid_b", "李四"))

    def test_weekly_goals_and_claim(self):
        week = local_week()
        goals = rules.weekly_goals("1@chatroom", week)
        reply = self.service.weekly(self.msg)
        self.assertIn("本周运势", reply)
        for goal in goals:
            if goal["key"] == "star":
                for index in range(1, int(goal["target"]) + 1):
                    self.store.add_star(
                        "1@chatroom", "wxid_a", "qinglong", index)
            else:
                self.feed(goal["key"], int(goal["target"]))
        reply = self.service.claim_weekly(self.msg)
        self.assertIn("周运奖励已领取", reply)
        self.assertIn("已经领过", self.service.claim_weekly(self.msg))

    def test_group_portrait_and_cp_board(self):
        self.service.set_sign(self.msg, "我是 天蝎座")
        self.service.set_mbti(self.msg, "我是 INFP")
        other = make_msg(sender_id="wxid_b", name="李四")
        self.service.set_sign(other, "我是 双鱼座")
        self.service.set_mbti(other, "我是 ENFP")
        portrait = self.service.group_portrait(self.msg)
        self.assertIn("【群像】", portrait)
        self.assertIn("天蝎座", portrait)
        board = self.service.cp_board(self.msg)
        self.assertIn("【CP榜】", board)
        self.assertIn("Top5", board)

    def test_observe_rolls_dice_and_collects(self):
        reply = self.service.observe(self.msg)
        self.assertIn("星骰", reply)
        dice = (
            self.store.collection_count(
                "1@chatroom", "wxid_a", "dice_planet")
            + self.store.collection_count(
                "1@chatroom", "wxid_a", "dice_zodiac")
            + self.store.collection_count(
                "1@chatroom", "wxid_a", "dice_house")
        )
        self.assertEqual(dice, 3)

    def test_sign_set_and_streak_rewards(self):
        for sign_id in range(1, 7):
            self.store.collect_sign("1@chatroom", "wxid_a", sign_id)
        gained = self.service._sign_set_rewards(
            "1@chatroom", "wxid_a", "张三")
        self.assertEqual(gained, 20)
        self.assertEqual(
            self.store.fortune("1@chatroom", "wxid_a"), 20)
        streak = self.service._streak_rewards(
            "1@chatroom", "wxid_a", "张三", local_day(), 3)
        self.assertEqual(streak, 5)

    def test_share_card(self):
        self.service.sign(self.msg)
        reply = self.service.share_card(self.msg)
        self.assertIn("今日签", reply)
        self.assertIn("#星盘", reply)

    def test_birthday_gives_noble_star_and_life_number(self):
        self.service.set_sign(self.msg, "设置生日 10-24")
        profile = self.store.get_profile("1@chatroom", "wxid_a")
        self.assertTrue(profile["noble_star"])
        self.assertEqual(profile["life_number"], 7)

    def test_identity_change_cooldown(self):
        self.service.set_sign(self.msg, "我是 天蝎座")
        reply = self.service.set_sign(self.msg, "我是 狮子座")
        self.assertIn("30 天内只能改一次", reply)
        self.service.set_mbti(self.msg, "我是 INFP")
        reply = self.service.set_mbti(self.msg, "我是 ENFP")
        self.assertIn("30 天内只能改一次", reply)

    def test_handler_target_from_mentioned_ids(self):
        msg = make_msg(sender_id="wxid_a", name="张三")
        msg["mentioned_ids"] = ["wxid_b"]
        reply = self.handler.handle("斗星", msg)
        self.assertIn("斗星", reply)


class BaziTests(KoiTestBase):
    """八字、八卦、天圆地方与四象。"""

    def test_parse_birth_formats(self):
        from src.koi import bazi
        parsed = bazi.parse_birth("1995-08-15 08:30")
        self.assertEqual((parsed["year"], parsed["month"], parsed["day"]),
                         (1995, 8, 15))
        self.assertEqual(parsed["hour"], 8)
        parsed = bazi.parse_birth("1995年8月15日 辰时")
        self.assertEqual(parsed["hour"], 8)
        parsed = bazi.parse_birth("19950815")
        self.assertIsNone(parsed["hour"])
        self.assertIsNone(bazi.parse_birth("明天"))

    def test_bazi_chart(self):
        from src.koi import bazi
        chart = bazi.bazi({
            "year": 1995, "month": 8, "day": 15, "hour": 8,
        })
        self.assertEqual(len(chart["pillars"]), 4)
        self.assertEqual(sum(chart["elements"].values()), 8)
        self.assertEqual(chart["zodiac"], "猪")
        self.assertIn(chart["quadrant"], ("青龙", "白虎", "朱雀", "玄武"))
        self.assertEqual(len(bazi.palace(chart)), 3)

    def test_hexagram_is_stable(self):
        from src.koi import bazi
        chart = bazi.bazi({
            "year": 1995, "month": 8, "day": 15, "hour": 8,
        })
        first = bazi.hexagram(chart, "seed")
        second = bazi.hexagram(chart, "seed")
        self.assertEqual(first["name"], second["name"])
        self.assertIn(first["name"], [n for row in bazi.HEXAGRAMS for n in row])
        self.assertTrue(first["changed"])

    def test_set_bazi_and_card(self):
        reply = self.service.set_bazi(self.msg, "设置八字 1995-08-15 08:30")
        self.assertIn("四柱", reply)
        self.assertIn("五行", reply)
        profile = self.store.get_profile("1@chatroom", "wxid_a")
        self.assertEqual(profile["birth_year"], 1995)
        self.assertEqual(profile["birth_hour"], 8)
        card = self.service.bazi_card(self.msg)
        self.assertIn("天圆地方", card)
        self.assertIn("年柱", card)

    def test_bazi_requires_birth(self):
        self.assertIn("还没设置出生时间", self.service.bazi_card(self.msg))
        self.assertIn("起卦前先设置", self.service.cast_hexagram(self.msg))
        self.assertIn("批命前先设置", self.service.daily_fortune(self.msg))

    def test_cast_hexagram_once_and_collect(self):
        self.service.set_bazi(self.msg, "设置八字 1995-08-15 08:30")
        reply = self.service.cast_hexagram(self.msg)
        self.assertIn("起卦", reply)
        self.assertEqual(
            self.store.collection_count(
                "1@chatroom", "wxid_a", "hexagram"), 1)
        self.assertIn("已经起过卦", self.service.cast_hexagram(self.msg))

    def test_daily_fortune_once(self):
        self.service.set_bazi(self.msg, "设置八字 1995-08-15 08:30")
        before = self.store.balance("1@chatroom", "wxid_a")
        reply = self.service.daily_fortune(self.msg)
        self.assertIn("【批命】", reply)
        self.assertGreater(
            self.store.balance("1@chatroom", "wxid_a"), before)
        self.assertIn("已经批过命", self.service.daily_fortune(self.msg))

    def test_hexagram_book(self):
        self.service.set_bazi(self.msg, "设置八字 1995-08-15 08:30")
        self.service.cast_hexagram(self.msg)
        reply = self.service.hexagram_book(self.msg)
        self.assertIn("【卦谱】已收集 1/64", reply)
        self.assertIn("六十四卦", reply)

    def test_handler_bazi_commands(self):
        self.assertIn("八字已记录", self.handler.handle(
            "设置八字 1995-08-15 08:30", self.msg))
        self.assertIn("【命盘】", self.handler.handle("八字", self.msg))
        self.assertIn("【批命】", self.handler.handle("批命", self.msg))
        self.assertIn("起卦", self.handler.handle("起卦", self.msg))
        self.assertIn("【卦谱】", self.handler.handle("卦谱", self.msg))

    def test_handler_detects_bazi_commands(self):
        self.assertTrue(looks_like_koi_command("设置八字 1995-08-15"))
        self.assertTrue(looks_like_koi_command("八字"))
        self.assertTrue(looks_like_koi_command("批命"))
        self.assertTrue(looks_like_koi_command("起卦"))
        self.assertTrue(looks_like_koi_command("卦谱"))


if __name__ == "__main__":
    unittest.main()
