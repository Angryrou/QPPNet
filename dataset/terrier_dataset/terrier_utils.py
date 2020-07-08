import collections, pickle
import pickle
import json
import numpy as np
from collections import Counter
from dataset.tpch_dataset.tpch_utils import TPCHDataSet
import dataset.terrier_dataset.terrier_query_info as tqi
SCALE = 100

MEM_ADJUST_MAP = getattr(tqi, "MEM_ADJUST_MAP")

def get_input_for_all(plan_dict):
    id_name = plan_dict["Node Type"].strip("tpch").upper()
    lst = getattr(tqi, id_name)
    feat_vec = []
    for op, feat in lst:
        feat_vec += feat
    if plan_dict["Node Type"] in MEM_ADJUST_MAP:
        feat_vec += [MEM_ADJUST_MAP[plan_dict["Node Type"]]]
    return feat_vec

with open('./dataset/terrier_dataset/terrier_group_dict.json', 'r') as f:
    pname_group_dict = json.load(f)

TR_GET_INPUT = collections.defaultdict(lambda: get_input_for_all)

###############################################################################
#       Parsing data from csv files that contain json output of queries       #
###############################################################################

class TerrierDataSet(TPCHDataSet):
    def __init__(self, opt):
        self.batch_size = opt.batch_size
        self.num_q = 1

        self.SCALE = SCALE
        self.input_func = TR_GET_INPUT

        fname = "execution.csv"
        all_data = self.get_all_plans(opt.data_dir + fname)
        enum, num_grp = self.grouping(all_data)

        count = Counter(enum)
        all_samp_num = count[min(count, key=lambda k:count[k])]

        all_groups = [[] for j in range(num_grp)]
        for j, grp_idx in enumerate(enum):
            all_groups[grp_idx].append(all_data[j])

        self.num_sample_per_q = int(all_samp_num * 0.9)

        self.grp_idxes = []
        train_data = []
        train_groups = [[] for j in range(num_grp)]
        test_groups = [[] for j in range(num_grp)]
        print(all_samp_num, self.num_sample_per_q)
        counter = 0
        for idx, grp in enumerate(all_groups):
            train_data += grp[:self.num_sample_per_q]
            train_groups[idx] += grp[:self.num_sample_per_q]
            test_groups[idx] += grp[self.num_sample_per_q: all_samp_num]
            self.grp_idxes += [idx] * self.num_sample_per_q
            counter += len(grp)

        self.num_grps = [num_grp]

        print([len(grp) for grp in train_groups])

        self.dataset = train_data
        self.datasize = len(self.dataset)

        if not opt.test_time:
            self.mean_range_dict = self.normalize(train_groups)
            with open('mean_range_dict.pickle', 'wb') as f:
                pickle.dump(self.mean_range_dict, f)
        else:
            with open(opt.mean_range_dict, 'rb') as f:
                self.mean_range_dict = pickle.load(f)

        print(self.mean_range_dict)

        test_dataset = [self.get_input(grp, 'dum') for grp in test_groups]
        self.test_dataset = test_dataset

    def get_input(self, data, i): # Helper for sample_data
        """
        Parameter: data is a list of plan_dict; all entry is from the same
        query template and thus have the same query plan;

        Returns: a single plan dict of similar structure, where each node has
            node_type     ---- a string, same as before
            feat_vec      ---- numpy array of size (batch_size x feat_size)
            children_plan ---- a list of children's plan_dicts where each plan_dict
                               has feat_vec encompassing that child in all
                               co-plans
        """
        new_samp_dict = {}

        new_samp_dict["node_type"] = data[0]["Operator Type"]
        new_samp_dict["real_node_type"] = data[0]["Node Type"]
        new_samp_dict["subbatch_size"] = len(data)
        feat_vec = np.array([self.input_func[jss["Node Type"]](jss) for jss in data])
        # print(feat_vec)
        # normalize feat_vec
        # print(new_samp_dict['node_type'])


        feat_vec = (feat_vec -
                    self.mean_range_dict[data[0]["Node Type"]][0]) \
                    / self.mean_range_dict[data[0]["Node Type"]][1]

        feat_vec += np.random.normal(0, 0.5, feat_vec.shape)

        total_time = [jss['Actual Total Time'] for jss in data]
        child_plan_lst = []
        if 'Plans' in data[0]:
            for i in range(len(data[0]['Plans'])):
                child_plan_dict = self.get_input([jss['Plans'][i] for jss in data], 'dum')
                child_plan_dict['is_subplan'] = False
                child_plan_lst.append(child_plan_dict)

        #print(i, [d["Node Type"] for d in data], feat_vec)
        new_samp_dict["feat_vec"] = np.array(feat_vec).astype(np.float32)
        new_samp_dict["children_plan"] = child_plan_lst
        new_samp_dict["total_time"] = np.array(total_time).astype(np.float32) / SCALE

        return new_samp_dict

    def normalize(self, train_groups): # compute the mean and std vec of each operator
        feat_vec_col = {operator : [] for operator in pname_group_dict}

        def parse_input(data):
            feat_vec = [self.input_func[data[0]["Node Type"]](jss) for jss in data]
            # print(feat_vec)
            if 'Plans' in data[0]:
                for i in range(len(data[0]['Plans'])):
                    parse_input([jss['Plans'][i] for jss in data])
            feat_vec_col[data[0]["Node Type"]].append(np.array(feat_vec).astype(np.float32))

        for grp in train_groups:
            parse_input(grp)

        def cmp_mean_range(feat_vec_lst):
          if len(feat_vec_lst) == 0:
            return (0, 1)
          else:
            total_vec = np.concatenate(feat_vec_lst)
            return (np.mean(total_vec, axis=0),
                    np.max(total_vec, axis=0)+np.finfo(np.float32).eps)

        mean_range_dict = {operator : cmp_mean_range(feat_vec_col[operator]) \
                           for operator in pname_group_dict}
        return mean_range_dict

    def get_all_plans(self, fname):
        jss = []
        currtree = dict()
        prev = None
        f = open(fname, 'r')
        lines = f.readlines()[1:]
        for line in lines:
            tokens = line.strip('\n').split(",")
            if len(tokens[0].split('_')) < 2:
                continue
            group = tokens[0].split('_')[1]
            if prev is not None:
                if prev != group:
                    jss.append(currtree)
                    currtree = dict()
                else:
                    currtree = {"Plans": [currtree]}
            currtree['Node Type'] = tokens[0]
            currtree['Operator Type'] = "operator_" + str(pname_group_dict[tokens[0]])
            currtree['Actual Total Time'] = tokens[-1]
            prev = group
        # jss is a list of json-transformed dicts, one for each query
        return jss
    def grouping(self, data):
        counter = 0
        enum = []
        unique = []
        for plan_dict in data:
            grp_num = "_".join(plan_dict['Node Type'].split("_")[1:-1])
            if grp_num in unique:
                enum.append(unique.index(grp_num))
            else:
                enum.append(counter)
                unique.append(grp_num)
                counter += 1
        print(counter)
        print(unique)
        return enum, counter

    ###############################################################################
    #       Sampling subbatch data from the dataset; total size is batch_size     #
    ###############################################################################
    def sample_data(self):
        # dataset: all queries used in training
        samp = np.random.choice(np.arange(self.datasize), self.batch_size, replace=False)
        #print(samp)
        samp_group = [[] for j in range(self.num_grps[0])]
        for idx in samp:
            grp_idx = self.grp_idxes[idx]
            samp_group[grp_idx].append(self.dataset[idx])

        parsed_input = []
        for grp in samp_group:
            # print(grp)
            if len(grp) != 0:
                parsed_input.append(self.get_input(grp, 'dum'))
        #print(parsed_input)
        return parsed_input