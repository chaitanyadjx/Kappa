import torch
from unixcoder import UniXcoder

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = UniXcoder("microsoft/unixcoder-base")
model.to(device)
print("Model loaded successfully on device:", device)

# Encode maximum function
# func = "def f(a,b): if a>b: return a else return b"
# tokens_ids = model.tokenize([func],max_length=512,mode="<encoder-only>")
# source_ids = torch.tensor(tokens_ids).to(device)
# tokens_embeddings,max_func_embedding = model(source_ids)

# # Encode minimum function
# func = "def f(a,b): if a<b: return a else return b"
# tokens_ids = model.tokenize([func],max_length=512,mode="<encoder-only>")
# source_ids = torch.tensor(tokens_ids).to(device)
# tokens_embeddings,min_func_embedding = model(source_ids)

# # Encode NL
# nl = "return maximum value"
# tokens_ids = model.tokenize([nl],max_length=512,mode="<encoder-only>")
# source_ids = torch.tensor(tokens_ids).to(device)
# tokens_embeddings,nl_embedding = model(source_ids)

context = """
def f(data,file_path):
    # write json data into file_path in python language
"""
tokens_ids = model.tokenize([context],max_length=512,mode="<decoder-only>")
source_ids = torch.tensor(tokens_ids).to(device)
prediction_ids = model.generate(source_ids, decoder_only=True, beam_size=3, max_length=128)
predictions = model.decode(prediction_ids)
print(context+predictions[0][0])



print(max_func_embedding.shape)
print(max_func_embedding)
