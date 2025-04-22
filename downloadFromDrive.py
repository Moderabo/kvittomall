import requests

def download_file_from_google_drive(file_id, destination):
    URL = "https://docs.google.com/uc?export=download&confirm=1"

    session = requests.Session()

    response = session.get(URL, params={"id": file_id}, stream=True)
    
    token = get_confirm_token(response)

    if token:
        params = {"id": file_id, "confirm": token}
        response = session.get(URL, params=params, stream=True)

    save_response_content(response, destination)

    return response


def get_confirm_token(response):
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            return value

    return None

def save_response_content(response, destination):
    CHUNK_SIZE = 32768
    print(response.headers)
    file_extension = response.headers["Content-Disposition"].split("=", -1)[-1].split(".",-1)[-1].replace('"',"")
    print(file_extension)
    if file_extension == "pdf":
        destination = destination + ".pdf"
    elif file_extension == "HEIC":
        destination = destination + ".HEIC"
    else:
        destination = destination + "_original.jpg"
    print(destination)

    with open(destination, "wb") as f:
        for chunk in response.iter_content(CHUNK_SIZE):
            if chunk:  # filter out keep-alive new chunks
                f.write(chunk)