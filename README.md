# Oracle Tag Analyzer
## Project Description
This project is a python script which will query scryfall for the oracle tag data and can run this information against a decklist.

## How To Run The Project
The script is run in python using the command
```python main.py -f <my deck list> -c <my cache file>```

On a first run you will not have a scryfall oracle tag cache file, but one will be created on your machine as cache.json. The retrieval of data is the most time consuming portion of the script and it is recommended to use the cache file when running the script.