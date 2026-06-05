from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import Dict, List
import json
from database import get_db, SessionLocal
from models import RoomCreate, JoinRoom, VoteAction
from game_manager import manager, GameLogic
import os

app = FastAPI(title="Word Explanation Game")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

game_logic = GameLogic(SessionLocal)

@app.get("/")
async def root():
    return FileResponse("game.html")

@app.post("/api/rooms/create")
async def create_room(room_data: RoomCreate):
    try:
        result = await game_logic.create_room(
            host_nickname=room_data.host_nickname,
            max_rounds=room_data.max_rounds,
            category=room_data.category,
            timer_seconds=room_data.timer_seconds
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/rooms/join")
async def join_room(join_data: JoinRoom):
    result = await game_logic.join_room(
        room_code=join_data.room_code,
        nickname=join_data.nickname
    )
    if not result:
        raise HTTPException(status_code=404, detail="Room not found")
    return result

@app.get("/api/rooms/{room_code}")
async def get_room_info(room_code: str):
    db = get_db()
    try:
        from database import Room
        room = db.query(Room).filter(Room.code == room_code).first()
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        
        return {
            "id": room.id,
            "code": room.code,
            "current_round": room.current_round,
            "max_rounds": room.max_rounds,
            "is_active": room.is_active,
            "category": room.category,
            "timer_seconds": room.timer_seconds,
            "players": [
                {
                    "id": p.id,
                    "nickname": p.nickname,
                    "score": p.score,
                    "is_ready": p.is_ready,
                    "is_explaining": p.is_explaining,
                    "avatar_color": p.avatar_color
                }
                for p in room.players
            ]
        }
    finally:
        db.close()

@app.get("/api/stats")
async def get_stats():
    db = get_db()
    try:
        from database import Room, Player
        total_rooms = db.query(Room).count()
        total_players = db.query(Player).count()
        active_rooms = db.query(Room).filter(Room.is_active == True).count()
        
        return {
            "total_rooms": total_rooms,
            "total_players": total_players,
            "active_rooms": active_rooms,
            "categories": list(game_logic.categories.keys())
        }
    finally:
        db.close()

@app.websocket("/ws/{room_code}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, player_id: int):
    print(f"🔌 WS CONNECT: room={room_code}, player={player_id}")
    await manager.connect(room_code, player_id, websocket)
    print(f"✅ WS CONNECTED: room={room_code}, player={player_id}")
    
    try:
        # Отправляем приветственное сообщение
        await manager.send_to_player(room_code, player_id, {
            "type": "chat_message",
            "data": {"player_id": 0, "message": "Welcome to the game!"}
        })
        
        await manager.broadcast_to_room(room_code, {
            "type": "player_joined",
            "data": {"player_id": player_id}
        }, exclude_player=player_id)
        
        while True:
            data = await websocket.receive_json()
            print(f"📨 RECEIVED: {data['type']}")
            
            if data["type"] == "ready":
                await manager.broadcast_to_room(room_code, {
                    "type": "player_ready",
                    "data": {"player_id": player_id}
                })
                
            elif data["type"] == "start_game":
                print(f"🎮 START GAME: room={room_code}")
                await game_logic.start_game(room_code)
                
            elif data["type"] == "vote":
                vote_data = data.get("data", {})
                await game_logic.handle_vote(
                    room_code=room_code,
                    voter_id=player_id,
                    word_guessed=vote_data.get("word_guessed", False)
                )
                
            elif data["type"] == "chat":
                print(f"💬 CHAT: {data['data']['message']}")
                # Отправляем сообщение ВСЕМ, включая отправителя
                await manager.broadcast_to_room(room_code, {
                    "type": "chat_message",
                    "data": {
                        "player_id": player_id,
                        "message": data["data"]["message"]
                    }
                })
    
    except WebSocketDisconnect:
        print(f"🔌 WS DISCONNECT: room={room_code}, player={player_id}")
        manager.disconnect(room_code, player_id)
        await manager.broadcast_to_room(room_code, {
            "type": "player_left",
            "data": {"player_id": player_id}
        })
    except Exception as e:
        print(f"❌ WS ERROR: {e}")

@app.get("/api/categories")
async def get_categories():
    return {"categories": list(game_logic.categories.keys())}

if __name__ == "__main__":
    import uvicorn
    import os
    
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
