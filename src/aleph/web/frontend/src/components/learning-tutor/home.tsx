import { useQuery } from "@tanstack/react-query";
import { sessionQueryOptions } from "../../lib/auth";
import { Recommendations } from "./recommendations";
import { useLearningTutor } from "./use-learning-tutor";

export function HomeTutor() {
  const user = useQuery(sessionQueryOptions).data?.user;
  return user?.feature_flags?.learning_tutor ? (
    <HomeTutorState key={user.id} account={user.id} />
  ) : null;
}
function HomeTutorState({ account }: { account: string }) {
  const tutor = useLearningTutor(account);
  return <Recommendations tutor={tutor} surface="home" />;
}
