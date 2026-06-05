from typing import Dict, Set
from fastapi import WebSocket
import random
import asyncio

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Dict[int, WebSocket]] = {}
        
    async def connect(self, room_code: str, player_id: int, websocket: WebSocket):
        await websocket.accept()
        if room_code not in self.active_connections:
            self.active_connections[room_code] = {}
        self.active_connections[room_code][player_id] = websocket
        print(f"🔌 CONNECTED: room={room_code}, player={player_id}, total in room={len(self.active_connections[room_code])}")
        
    def disconnect(self, room_code: str, player_id: int):
        if room_code in self.active_connections:
            self.active_connections[room_code].pop(player_id, None)
            if not self.active_connections[room_code]:
                del self.active_connections[room_code]
                
    async def broadcast_to_room(self, room_code: str, message: dict, exclude_player: int = None):
        if room_code in self.active_connections:
            players = list(self.active_connections[room_code].items())
            print(f"📡 BROADCAST room={room_code}, type={message.get('type')}, to {len(players)} players")
            for player_id, connection in players:
                if player_id != exclude_player:
                    try:
                        await connection.send_json(message)
                        print(f"  ✅ Sent to player {player_id}")
                    except Exception as e:
                        print(f"  ❌ Error sending to player {player_id}: {e}")
        else:
            print(f"  ❌ Room {room_code} not found")
    
    async def send_to_player(self, room_code: str, player_id: int, message: dict):
        if room_code in self.active_connections and player_id in self.active_connections[room_code]:
            try:
                await self.active_connections[room_code][player_id].send_json(message)
                print(f"📨 Sent to player {player_id}: {message.get('type')}")
            except Exception as e:
                print(f"❌ Error sending to player {player_id}: {e}")

manager = ConnectionManager()

class GameLogic:
    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory
        self.categories = {
            "Mixed": ["Butterfly", "Airplane", "Cat", "Robot", "Computer", "Basketball", "Guitar", "Book", "Sun", "Moon"],
            "Animals": ["Elephant", "Giraffe", "Penguin", "Kangaroo", "Dolphin", "Tiger", "Panda", "Eagle"],
            "Food": ["Pizza", "Ice Cream", "Sushi", "Burger", "Cake", "Watermelon", "Chocolate"]
        }
        
    def generate_room_code(self) -> str:
        return ''.join([str(random.randint(0, 9)) for _ in range(6)])
    
    def get_random_phrases(self, category: str, count: int = 30) -> list:
        if category in self.categories:
            pool = self.categories[category]
        else:
            pool = self.categories["Mixed"]
        while len(pool) < count:
            pool.append(random.choice(pool))
        return random.sample(pool, min(count, len(pool)))
    
    async def create_room(self, host_nickname: str, max_rounds: int, category: str, timer_seconds: int):
        db = self.db_session_factory()
        try:
            from database import Room, Player, Phrase
            
            room = Room(code=self.generate_room_code(), max_rounds=max_rounds, category=category, timer_seconds=timer_seconds)
            db.add(room)
            db.flush()
            
            colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7"]
            player = Player(nickname=host_nickname, room_id=room.id, is_ready=True, avatar_color=random.choice(colors))
            db.add(player)
            db.flush()
            
            room.host_player_id = player.id
            
            phrases = self.get_random_phrases(category, max_rounds * 3)
            for i, phrase_text in enumerate(phrases):
                phrase = Phrase(text=phrase_text, room_id=room.id, round_number=(i // 3) + 1, category=category)
                db.add(phrase)
            
            db.commit()
            return {"room_id": room.id, "room_code": room.code, "player_id": player.id, "max_rounds": max_rounds, "category": category, "timer_seconds": timer_seconds}
        finally:
            db.close()
    
    async def join_room(self, room_code: str, nickname: str):
        db = self.db_session_factory()
        try:
            from database import Room, Player
            
            room = db.query(Room).filter(Room.code == room_code, Room.is_active == True).first()
            if not room:
                return None
            
            colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7"]
            player = Player(nickname=nickname, room_id=room.id, avatar_color=random.choice(colors))
            db.add(player)
            db.commit()
            db.refresh(player)
            
            return {"room_id": room.id, "player_id": player.id, "player_nickname": player.nickname, "max_rounds": room.max_rounds, "category": room.category, "timer_seconds": room.timer_seconds, "avatar_color": player.avatar_color}
        finally:
            db.close()
    
    async def start_game(self, room_code: str):
        print(f"🎮 START GAME: {room_code}")
        db = self.db_session_factory()
        try:
            from database import Room, Player, Phrase
            
            room = db.query(Room).filter(Room.code == room_code).first()
            if not room:
                return
            
            players = db.query(Player).filter(Player.room_id == room.id).all()
            if len(players) < 2:
                return
            
            first_explainer = players[0]
            first_explainer.is_explaining = True
            room.current_round = 1
            db.commit()
            
            phrase = db.query(Phrase).filter(Phrase.room_id == room.id, Phrase.round_number == 1, Phrase.is_used == False).first()
            
            print(f"📢 Broadcasting game_starting to room {room_code}")
            await manager.broadcast_to_room(room_code, {
                "type": "game_starting",
                "data": {
                    "current_round": 1,
                    "max_rounds": room.max_rounds,
                    "timer_seconds": room.timer_seconds,
                    "explainer": {"id": first_explainer.id, "nickname": first_explainer.nickname, "avatar_color": first_explainer.avatar_color}
                }
            })
            
            if phrase:
                await manager.send_to_player(room_code, first_explainer.id, {
                    "type": "new_phrase",
                    "data": {"phrase": phrase.text, "difficulty": phrase.difficulty if hasattr(phrase, 'difficulty') else 1}
                })
            
            # Запускаем таймер
            print(f"⏱️ Starting timer for room {room_code}")
            asyncio.create_task(self._run_timer(room_code, room.timer_seconds))
            
        finally:
            db.close()
    
    async def _run_timer(self, room_code: str, seconds: int):
        print(f"⏱️ TIMER RUNNING: room={room_code}, seconds={seconds}")
        for remaining in range(seconds, -1, -1):
            await manager.broadcast_to_room(room_code, {
                "type": "timer_update",
                "data": {"seconds": remaining, "total": seconds}
            })
            if remaining > 0:
                await asyncio.sleep(1)
        print(f"⏱️ TIMER DONE: room={room_code}")
        await self.end_round(room_code)
    
    async def end_round(self, room_code: str):
        print(f"🏁 END ROUND: {room_code}")
        db = self.db_session_factory()
        try:
            from database import Room, Player, Phrase
            
            room = db.query(Room).filter(Room.code == room_code).first()
            if not room:
                return
            
            current_explainer = db.query(Player).filter(Player.room_id == room.id, Player.is_explaining == True).first()
            if current_explainer:
                current_explainer.is_explaining = False
            
            players = db.query(Player).filter(Player.room_id == room.id).all()
            scores = {p.nickname: p.score for p in players}
            
            await manager.broadcast_to_room(room_code, {
                "type": "round_end",
                "data": {"round": room.current_round, "scores": scores}
            })
            
            await asyncio.sleep(2)
            
            if room.current_round < room.max_rounds:
                room.current_round += 1
                db.commit()
                
                next_explainer = players[room.current_round % len(players)]
                next_explainer.is_explaining = True
                db.commit()
                
                await manager.broadcast_to_room(room_code, {
                    "type": "new_round",
                    "data": {
                        "round": room.current_round,
                        "max_rounds": room.max_rounds,
                        "explainer": {"id": next_explainer.id, "nickname": next_explainer.nickname, "avatar_color": next_explainer.avatar_color}
                    }
                })
                
                phrase = db.query(Phrase).filter(Phrase.room_id == room.id, Phrase.round_number == room.current_round, Phrase.is_used == False).first()
                if phrase:
                    await manager.send_to_player(room_code, next_explainer.id, {
                        "type": "new_phrase",
                        "data": {"phrase": phrase.text}
                    })
                
                asyncio.create_task(self._run_timer(room_code, room.timer_seconds))
            else:
                winner = max(players, key=lambda p: p.score)
                await manager.broadcast_to_room(room_code, {
                    "type": "game_end",
                    "data": {"final_scores": scores, "winner": winner.nickname, "winner_color": winner.avatar_color}
                })
                room.is_active = False
                db.commit()
        finally:
            db.close()
    
    async def handle_vote(self, room_code: str, voter_id: int, word_guessed: bool):
    db = self.db_session_factory()
    try:
        from database import Room, Player, Phrase
        
        room = db.query(Room).filter(Room.code == room_code).first()
        if not room:
            return
        
        explainer = db.query(Player).filter(Player.room_id == room.id, Player.is_explaining == True).first()
        
        if explainer and word_guessed:
            voter = db.query(Player).filter(Player.id == voter_id).first()
            
            # Проверяем, что голосует не сам объясняющий
            if voter and voter.id != explainer.id:
                voter.score += 2       # Угадавший получает 2 очка
                explainer.score += 1   # Объясняющий получает 1 очко
                db.commit()
                
                print(f"🏆 SCORE: {voter.nickname}=+2, {explainer.nickname}=+1")
                
                await manager.broadcast_to_room(room_code, {
                    "type": "score_update",
                    "data": {"scores": {p.id: p.score for p in room.players}}
                })
                
                # Отмечаем фразу как использованную
                phrase = db.query(Phrase).filter(
                    Phrase.room_id == room.id, 
                    Phrase.round_number == room.current_round, 
                    Phrase.is_used == False
                ).first()
                
                if phrase:
                    phrase.is_used = True
                    db.commit()
                    await manager.broadcast_to_room(room_code, {
                        "type": "word_guessed",
                        "data": {
                            "phrase": phrase.text, 
                            "guessed_by": voter.nickname,
                            "voter_score": voter.score,
                            "explainer_score": explainer.score
                        }
                    })
    finally:
        db.close()
