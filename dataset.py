from datasets import load_dataset
import torch
import pickle
from utils import compute_F1, compute_exact_match
from tqdm import tqdm
import pdb;
from torch.utils.tensorboard import SummaryWriter
writer = SummaryWriter()
batch_size = 4
def preprocessing_dataset(name, dataset):
    context = []
    question = []
    answer = {'answer_start' : [], 'answer_end' : []}
    print(f"preprocessing {name} data")
    if name == "mrqa":
        for i, (c, q, ans) in tqdm(enumerate(zip(dataset['context'], dataset['question'],dataset['answers'])), total= len(dataset['context'])):
            for a in ans:
                context.append(c)
                question.append(q)
                answer['answer_start'].append(c.find(a))
                answer['answer_end'].append(c.find(a) + len(a))

    elif name == 'coqa':
        for i, (c,qs,ans) in tqdm(enumerate(zip(dataset['story'],dataset['questions'],dataset['answers'])), total=len(dataset['story'])):
            for i in range(len(qs)):
                context.append(c)
                question.append(qs[i])
                answer['answer_start'].append(ans['answer_start'][i])
                answer['answer_end'].append(ans['answer_end'][i])
    
    elif name == "squad2":
        for i, (c, q, ans) in tqdm(enumerate(zip( dataset['context'], dataset['question'], dataset['answers'])), total=len(dataset['context'])):
            for s_idx, text in zip(ans['answer_start'],ans['text']):
                context.append(c)
                question.append(q)
                answer['answer_start'].append(s_idx)
                answer['answer_end'].append(s_idx + len(text))
    
    else :
        print("wrong dataset name")
        exit()
    return context, question, answer


def add_token_positions(encodings, answers):
    start_positions = []
    end_positions = []

    for i in range(len(answers['answer_start'])):
        # char의 index로 되어있던것을 token의 index로 찾아준다.
        start_positions.append(encodings.char_to_token(i, answers['answer_start'][i])) #batch_index, char_index
        end_positions.append(encodings.char_to_token(i, answers['answer_end'][i] - 1))

        # if start position is None, the answer passage has been truncated
        if start_positions[-1] is None:
            start_positions[-1] = tokenizer.model_max_length
        if end_positions[-1] is None:
            end_positions[-1] = tokenizer.model_max_length

    encodings.update({'start_positions': start_positions, 'end_positions': end_positions})


class SquadDataset(torch.utils.data.Dataset):
    def __init__(self, encodings):
        self.encodings = encodings

    def __getitem__(self, idx):
        return {key: torch.tensor(val[idx]) for key, val in self.encodings.items()}

    def __len__(self):
        return len(self.encodings.input_ids)



if __name__ =="__main__":
    train_contexts, train_questions, train_answers = [], [] , {'answer_start' : [], 'answer_end' : []}
    val_contexts, val_questions, val_answers = [], [] , {'answer_start' : [], 'answer_end' : []}
    try:
        print("Load processed data")
        with open('data/preprocessed_train', 'rb') as f:
            train_contexts, train_questions, train_answers = pickle.load(f)
        with open('data/preprocessed_val', 'rb') as f:
            val_contexts, val_questions, val_answers = pickle.load(f)
        print("Load Finish")
    except:
        print("There is no preprocessed data.")
        mrqa_datasets = load_dataset('mrqa')
        coqa_datasets = load_dataset('coqa')
        # quac_datasets = load_dataset('quac') # 형식이 약간 달라서 일단 제외!!
        squad2_datasets = load_dataset('squad')


        # for name, dataset in zip(['mrqa', 'coqa','squad2'], [mrqa_datasets, coqa_datasets, squad2_datasets]):
        for name, dataset in zip(['squad2'], [squad2_datasets]):
        
            context, question, answer = preprocessing_dataset(name, dataset['train'])
            assert len(context) == len(question) == len(answer['answer_start']) == len(answer['answer_end'])

            train_contexts.extend(context)
            train_questions.extend(question)
            train_answers['answer_start'].extend(answer['answer_start'])
            train_answers['answer_end'].extend(answer['answer_end'])


            context, question, answer = preprocessing_dataset(name, dataset['validation'])
            assert len(context) == len(question) == len(answer['answer_start']) == len(answer['answer_end'])


            val_contexts.extend(context)
            val_questions.extend(question)
            val_answers['answer_start'].extend(answer['answer_start'])
            val_answers['answer_end'].extend(answer['answer_end'])


        train = [train_contexts, train_questions, train_answers]
        val = [val_contexts, val_questions, val_answers]

        print("Load Tokenizer")
        from transformers import BertTokenizerFast
        tokenizer = BertTokenizerFast.from_pretrained('bert-base-uncased')

        print("Encoding dataset")
        train_encodings = tokenizer(train_contexts, train_questions, truncation='only_first', padding=True) # [CLS] context [SEP] question
        val_encodings = tokenizer(val_contexts, val_questions, truncation='only_first', padding=True)

        print("Add token postion")
        add_token_positions(train_encodings, train_answers)
        add_token_positions(val_encodings, val_answers)



        # save
        with open('data/preprocessed_train.pickle', 'wb') as f:
            pickle.dump(train, f, pickle.HIGHEST_PROTOCOL)

        # save
        with open('data/preprocessed_val.pickle', 'wb') as f:
            pickle.dump(val, f, pickle.HIGHEST_PROTOCOL)

    ## load model and tokeinzer

    
    
        


    train_dataset = SquadDataset(train_encodings)
    val_dataset = SquadDataset(val_encodings)

    from transformers import BertForQuestionAnswering
    model = BertForQuestionAnswering.from_pretrained("bert-base-uncased")


    from torch.utils.data import DataLoader
    from transformers import AdamW

    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')

    model.to(device)
    model.train()

    train_loader = DataLoader(train_dataset, batch_size, shuffle=True)
    dev_loader = DataLoader(val_dataset, batch_size, shuffle=True)

    optim = AdamW(model.parameters(), lr=5e-5)

    patience = 3
    max_epoch = 100
    penalty = 0
    max_F1 = 0
    log_interval = 1000

    for epoch in range(max_epoch):
        # model.train()
        loss_sum = 0
        t_train_loader = tqdm(train_loader)
        for iter, batch in enumerate(t_train_loader):
            optim.zero_grad()
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            start_positions = batch['start_positions'].to(device)
            end_positions = batch['end_positions'].to(device)
            outputs = model(input_ids, attention_mask=attention_mask, start_positions=start_positions, end_positions=end_positions)
            loss = outputs[0]
            loss.backward()
            optim.step()
            t_train_loader.set_description("Loss %.04f" (loss))
            # if iter%1000 ==0:
            #     print(loss)

        model.eval()
        EM = 0
        F1 = 0
        pdb.set_trace()
        print("Validation start")
        with torch.no_grad():
            t_dev_loader = tqdm(dev_loader)
            for iter,batch in enumerate(t_dev_loader):
                input_ids = batch['input_ids'].to(device)
                attention_mask = batch['attention_mask'].to(device)
                start_positions = batch['start_positions'].to(device)
                end_positions = batch['end_positions'].to(device)
                outputs = model(input_ids, attention_mask=attention_mask, start_positions=start_positions, end_positions=end_positions)

                pred_start_positions = torch.argmax(outputs['start_logits'], dim=1).to('cpu')
                pred_end_positions = torch.argmax(outputs['end_logits'], dim=1).to('cpu')
                for b in range(len(pred_start_positions)):
                    ans_text = tokenizer.convert_tokens_to_string(tokenizer.convert_ids_to_tokens(input_ids[b][start_positions[b]:end_positions[b]+1]))
                    pred_text = tokenizer.convert_tokens_to_string(tokenizer.convert_ids_to_tokens(input_ids[b][pred_start_positions[b]:pred_end_positions[b]+1]))
                    EM += compute_exact_match(pred_text, ans_text)
                    F1 += compute_F1(pred_text, ans_text)
                loss = outputs[0].to('cpu')
                t_dev_loader.set_description("Loss %.04f  | step %d" % (loss, iter))

            print(f"EM= {EM/iter}")
            print(f"F1 = {F1/iter}")

            writer.add_scalar("Loss/train", loss_sum/iter, epoch)
            writer.add_scalar("EM/train", EM/iter, epoch)
            writer.add_scalar("F1/train", F1/iter, epoch)

            if (F1/iter) > max_F1:
                max_F1 = F1/iter
                penalty = 0
                torch.save(model, "model/qa_extraction.pt")
            else:
                penalty +=1
                if penalty>patience:
                    print("early stopping")
                    break
            writer.close()

            # model = torch.load(PATH)
            # model.eval()



