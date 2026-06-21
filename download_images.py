import requests
import os

folder = "uploads/posts"
os.makedirs(folder, exist_ok=True)

for i in range(1, 501):
    url = f"https://picsum.photos/600/600?random={i}"

    response = requests.get(url)

    with open(f"{folder}/post{i}.jpg", "wb") as f:
        f.write(response.content)

    print(f"Downloaded post{i}.jpg")

print("500 images downloaded successfully!")