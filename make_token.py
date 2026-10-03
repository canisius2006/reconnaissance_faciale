from livekit import api

token = (
    api.AccessToken("devkey", "secret")  # clés du mode --dev
    .with_identity("test_user")           # qui est ce participant
    .with_grants(api.VideoGrants(
        room_join=True,                   # droit de rejoindre une room
        room="test_room",                 # cette room précisément
    ))
    .to_jwt()
)

print(token)